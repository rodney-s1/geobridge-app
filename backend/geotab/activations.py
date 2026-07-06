"""
activations.py — Activations tab backend
=========================================

Provides the /api/activations endpoint showing devices that came online
(firstDeviceActivationDate) within a requested date range.

Data source: the existing MyAdmin contract sync cache (_sync_cache["contracts"])
— no separate API call needed. The same data that powers Customers, Invoices,
and Reconciliation is filtered here by firstDeviceActivationDate date range.

This makes the Activations tab:
  - Instant  (reads from the in-memory cache, zero extra API calls)
  - Accurate (same contracts, same SKU resolution logic as invoices.py)
  - Complete (has full contract context: customer, plan, promoCode, dates)

Each record is enriched with:
  - Resolved QB SKU key (same 4-tier logic as invoices.py)
  - Proration details (daysActive, prorateFactor, proratedCharge)
  - Customer billing type
  - All contract fields visible on the MyAdmin Activation History page
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from .auth import session_store
from .customers import (
    MYADMIN_ACCOUNT,
    _sync_cache,
    _clean_name,
    _strip_han_cs,
    _strip_sub_account_suffix,
    billing_type_overrides,
    billing_date_overrides,
    first_connect_date_overrides,
    qb_customers,
)
from .invoices import (
    _build_indices,
    _resolve_sku,
    _sku_from_serial,
    _is_dm_serial,
    _prorate_factor,
    _GE_GF_BASE_SKU,
    _GF_SKU_REMAP,
    EXCLUDED_CATEGORIES,
)
from .reconciliation import _normalize, _resolve_price

router = APIRouter()

# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _safe_date_str(raw) -> str:
    """Trim ISO datetime to YYYY-MM-DD; return '' for .NET sentinel 0001-01-01."""
    s = (raw or "")[:10]
    return "" if s.startswith("0001") else s


def _parse_date(s: str) -> Optional[date]:
    """Parse YYYY-MM-DD string to date, return None on failure."""
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
#  SKU resolution (mirrors invoices.py _generate_prorated_invoice)             #
# --------------------------------------------------------------------------- #

def _resolve_activation_sku(
    serial: str,
    customer_norm: str,
    rate_plan_code: str,
    billing_plan: str,
    mapping_index: dict,
    cust_map_index: dict,
    plan_promo_index: dict,
) -> str:
    serial_upper = (serial or "").strip().upper()
    _gf_serial   = serial_upper.startswith("GF")
    _ge_serial   = serial_upper.startswith("GE")

    if _is_dm_serial(serial):
        return "EXCLUDED (Digital Matter)"

    if not rate_plan_code and (_gf_serial or _ge_serial):
        return _GE_GF_BASE_SKU["GF" if _gf_serial else "GE"]

    sku = _sku_from_serial(serial)

    if not sku:
        sku = (
            _resolve_sku(customer_norm, rate_plan_code, mapping_index, cust_map_index,
                         billing_plan, plan_promo_index)
            or _resolve_sku(customer_norm, billing_plan, mapping_index, cust_map_index)
            or "UNMAPPED"
        )

    if _gf_serial and sku in _GF_SKU_REMAP:
        sku = _GF_SKU_REMAP[sku]

    return sku


# --------------------------------------------------------------------------- #
#  Customer billing-type lookup                                                 #
# --------------------------------------------------------------------------- #

def _get_billing_type(company_id: str, raw_name: str) -> str:
    clean_name     = _clean_name(raw_name)
    _after_sub     = _strip_sub_account_suffix(clean_name)
    qb_lookup_name = _strip_han_cs(_after_sub)
    is_han_cs      = _after_sub.strip().lower().endswith("{han-cs}")

    return (
        billing_type_overrides.get(company_id)
        or (qb_customers.get(_normalize(qb_lookup_name)) or {}).get("billingType")
        or ("Han-CS" if is_han_cs else "Unknown")
    )


# --------------------------------------------------------------------------- #
#  Enrich a single contract record into an activation row                      #
# --------------------------------------------------------------------------- #

def _enrich_contract(
    contract: dict,
    activation_date_str: str,
    activation_date_obj: date,
    catalog_index: dict,
    ovr_index: dict,
    mapping_index: dict,
    cust_map_index: dict,
    full_path_index: dict,
    sku_desc_index: dict,
    category_index: dict,
    plan_promo_index: dict,
) -> dict:
    # --- Device ---
    device    = contract.get("device") or {}
    serial    = device.get("serialNumber") or ""
    imei      = str(device.get("id") or "")

    # --- Customer ---
    uc         = contract.get("userContact") or {}
    company    = uc.get("userCompany") or {}
    company_id = str(company.get("id") or "")
    company_name = company.get("name") or ""

    # --- Database ---
    ldd = contract.get("latestDeviceDatabase") or {}
    active_db = ldd.get("databaseName") or ""

    # --- Plan ---
    adp          = contract.get("activeDevicePlan") or {}
    active_plan  = adp.get("name") or ""
    rate_plan    = (contract.get("promoCode") or "").upper()

    # Strip ": Live" / ": Demo" suffix for billing plan
    billing_plan = active_plan.split(":")[0].strip() if active_plan else ""

    # --- Dates (raw from contract) ---
    raw_fcd = _safe_date_str(contract.get("firstDeviceActivationDate"))
    raw_bsd = _safe_date_str(contract.get("billingStartDate"))
    raw_start = _safe_date_str(contract.get("startDate"))
    raw_end   = _safe_date_str(contract.get("endDate"))

    # Apply manual overrides (same as customers.py display logic)
    serial_key = serial.strip().upper()
    fcd_override = first_connect_date_overrides.get(serial_key)
    bsd_override = billing_date_overrides.get(serial_key)
    display_fcd  = fcd_override or raw_fcd
    display_bsd  = bsd_override or raw_bsd

    # --- Billing type ---
    billing_type = _get_billing_type(company_id, company_name)

    # --- SKU resolution ---
    customer_norm = _normalize(company_name)
    sku_key = _resolve_activation_sku(
        serial=serial,
        customer_norm=customer_norm,
        rate_plan_code=rate_plan,
        billing_plan=billing_plan,
        mapping_index=mapping_index,
        cust_map_index=cust_map_index,
        plan_promo_index=plan_promo_index,
    )

    sku_category     = category_index.get(sku_key, "")
    excluded_category = sku_category in EXCLUDED_CATEGORIES
    item_code        = full_path_index.get(sku_key, sku_key)
    sku_desc         = sku_desc_index.get(sku_key, sku_key)

    # --- Proration ---
    proration = None
    is_pilot  = "PILOT" in rate_plan
    if (not excluded_category
            and sku_key not in ("UNMAPPED", "EXCLUDED (Digital Matter)")
            and not is_pilot):
        b_year  = activation_date_obj.year
        b_month = activation_date_obj.month
        monthly_rate, price_source = _resolve_price(
            customer_norm, sku_key, ovr_index, catalog_index
        )
        if monthly_rate:
            days_active, days_in_month, factor = _prorate_factor(
                activation_date_obj, b_year, b_month
            )
            proration = {
                "billingMonth":   f"{b_year}-{b_month:02d}",
                "activationDate": activation_date_str,
                "daysActive":     days_active,
                "daysInMonth":    days_in_month,
                "prorateFactor":  round(factor, 6),
                "monthlyRate":    monthly_rate,
                "proratedCharge": round(monthly_rate * factor, 2),
                "priceSource":    price_source,
            }

    return {
        # Device identity
        "serialNumber":     serial,
        "imei":             imei,

        # Customer
        "companyId":        company_id,
        "customerName":     company_name,
        "activeDatabase":   active_db,
        "billingType":      billing_type,

        # Plan info
        "activePlan":       active_plan,
        "ratePlanCode":     rate_plan,
        "isPilot":          is_pilot,

        # Dates
        "firstConnectDate":   display_fcd,
        "billingStartDate":   display_bsd,
        "contractStartDate":  raw_start,
        "contractEndDate":    raw_end,
        "activationDate":     activation_date_str,

        # SKU
        "skuKey":           sku_key,
        "itemCode":         item_code,
        "skuDesc":          sku_desc,
        "skuCategory":      sku_category,
        "excludedCategory": excluded_category,

        # Proration
        "proration":        proration,
    }


# --------------------------------------------------------------------------- #
#  Main activation derivation from cached contracts                            #
# --------------------------------------------------------------------------- #

def _get_activation_date(contract: dict) -> Optional[str]:
    """
    Return the best activation date string for a contract, applying the same
    override priority as invoices.py:
      1. first_connect_date_overrides (user-set)
      2. firstDeviceActivationDate from API
      Returns None if no valid date.
    """
    device    = contract.get("device") or {}
    serial    = (device.get("serialNumber") or "").strip().upper()

    fcd_override = first_connect_date_overrides.get(serial)
    api_fcd      = _safe_date_str(contract.get("firstDeviceActivationDate"))
    return fcd_override or api_fcd or None


# --------------------------------------------------------------------------- #
#  Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("/activations")
async def get_activations(
    from_date: str = Query(
        default="",
        description="Start date YYYY-MM-DD (defaults to first day of current month)",
        alias="fromDate",
    ),
    to_date: str = Query(
        default="",
        description="End date YYYY-MM-DD (defaults to today)",
        alias="toDate",
    ),
    billing_type: str = Query(
        default="",
        description="Filter by billing type (e.g. 'Charge Upon Activation', 'Hanover'). "
                    "Empty = all types.",
        alias="billingType",
    ),
    customer_id: str = Query(
        default="",
        description="Filter to a specific MyAdmin company ID.",
        alias="customerId",
    ),
    include_terminated: bool = Query(
        default=False,
        description="Include terminated contracts. Default false.",
        alias="includeTerminated",
    ),
):
    """
    Returns devices that had their first activation (firstDeviceActivationDate)
    within the requested date range, enriched with SKU resolution and proration.

    Data source: cached MyAdmin contracts (_sync_cache). Run a MyAdmin sync
    first if the cache is empty or stale.

    Date defaults: first day of the current month → today.
    """
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in to MyAdmin")

    all_contracts: List[dict] = _sync_cache.get("contracts") or []
    if not all_contracts:
        raise HTTPException(
            status_code=503,
            detail="No contract data cached. Please run a MyAdmin sync first.",
        )

    # Default date range: first of current month → today
    today = date.today()
    if not from_date:
        from_date = date(today.year, today.month, 1).isoformat()
    if not to_date:
        to_date = today.isoformat()

    from_dt = _parse_date(from_date)
    to_dt   = _parse_date(to_date)
    if not from_dt or not to_dt:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")
    if from_dt > to_dt:
        raise HTTPException(status_code=400, detail="fromDate must be ≤ toDate")

    # Build SKU indices
    (catalog_index, ovr_index, mapping_index,
     cust_map_index, full_path_index, sku_desc_index,
     category_index, plan_promo_index) = _build_indices()

    results: List[dict] = []

    for contract in all_contracts:
        # Terminated filter
        is_terminated = contract.get("isTerminated", False)
        if is_terminated and not include_terminated:
            continue

        # Get the activation date
        act_str = _get_activation_date(contract)
        if not act_str:
            continue  # Never activated — no firstDeviceActivationDate

        act_obj = _parse_date(act_str)
        if not act_obj:
            continue

        # Date range filter
        if not (from_dt <= act_obj <= to_dt):
            continue

        # Company ID filter
        if customer_id:
            uc  = contract.get("userContact") or {}
            cid = str((uc.get("userCompany") or {}).get("id") or "")
            if cid != customer_id:
                continue

        # Enrich
        try:
            row = _enrich_contract(
                contract,
                activation_date_str=act_str,
                activation_date_obj=act_obj,
                catalog_index=catalog_index,
                ovr_index=ovr_index,
                mapping_index=mapping_index,
                cust_map_index=cust_map_index,
                full_path_index=full_path_index,
                sku_desc_index=sku_desc_index,
                category_index=category_index,
                plan_promo_index=plan_promo_index,
            )
        except Exception:
            continue

        # Billing type filter
        if billing_type and row["billingType"] != billing_type:
            continue

        results.append(row)

    # Sort: earliest activation date first
    results.sort(key=lambda r: r.get("activationDate") or "")

    # Summary stats
    total         = len(results)
    total_prorated = round(sum(
        (r["proration"] or {}).get("proratedCharge", 0.0)
        for r in results if r.get("proration")
    ), 2)
    unmapped_cnt  = sum(1 for r in results if r["skuKey"] == "UNMAPPED")
    excluded_cnt  = sum(1 for r in results if r["excludedCategory"])
    pilot_cnt     = sum(1 for r in results if r.get("isPilot"))

    # Cache age info
    cache_fetched_at = _sync_cache.get("fetched_at")
    cache_age_hours  = (
        round((__import__("time").time() - cache_fetched_at) / 3600, 1)
        if cache_fetched_at else None
    )

    return {
        "fromDate":           from_date,
        "toDate":             to_date,
        "totalRecords":       total,
        "totalProratedAmount": total_prorated,
        "unmappedCount":      unmapped_cnt,
        "excludedCount":      excluded_cnt,
        "pilotCount":         pilot_cnt,
        "cacheAgeHours":      cache_age_hours,
        "totalContractsInCache": len(all_contracts),
        "records":            results,
    }


@router.get("/activations/summary")
async def get_activations_summary(
    from_date: str = Query(default="", alias="fromDate"),
    to_date:   str = Query(default="", alias="toDate"),
):
    """
    Activations grouped by customer — count + total prorated amount.
    Useful for a dashboard overview.
    """
    result = await get_activations(
        from_date=from_date,
        to_date=to_date,
    )

    by_customer: Dict[str, dict] = defaultdict(lambda: {
        "customerName": "",
        "billingType":  "",
        "count":        0,
        "totalProrated": 0.0,
        "skus": {},
    })

    for r in result["records"]:
        cid = r["companyId"] or r["customerName"]
        by_customer[cid]["customerName"]   = r["customerName"]
        by_customer[cid]["billingType"]    = r["billingType"]
        by_customer[cid]["count"]         += 1
        if r.get("proration"):
            by_customer[cid]["totalProrated"] += r["proration"].get("proratedCharge", 0.0)
        sku = r["skuKey"]
        by_customer[cid]["skus"][sku] = by_customer[cid]["skus"].get(sku, 0) + 1

    summary_list = [
        {
            "companyId":       cid,
            "customerName":    d["customerName"],
            "billingType":     d["billingType"],
            "activationCount": d["count"],
            "totalProrated":   round(d["totalProrated"], 2),
            "skus":            d["skus"],
        }
        for cid, d in by_customer.items()
    ]
    summary_list.sort(key=lambda x: x["activationCount"], reverse=True)

    return {
        "fromDate":  result["fromDate"],
        "toDate":    result["toDate"],
        "cacheAgeHours": result["cacheAgeHours"],
        "customers": summary_list,
    }
