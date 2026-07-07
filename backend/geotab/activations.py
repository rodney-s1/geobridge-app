# -*- coding: utf-8 -*-
"""
activations.py — Activations tab backend
=========================================

Provides the /api/activations endpoint showing devices that had a contract
request (activation, plan change, etc.) within the requested date range.

Data source: GetDeviceContractAutoRequests (MyAdmin API) — the same data
that powers the "Device Contract Request History" page in the MyAdmin UI.
This means every row in Activations corresponds to an actual contract event
logged by MyAdmin (not a derived inference from contract start dates).

Each record is enriched with:
  - Resolved QB SKU key  (same 4-tier logic as invoices.py)
  - Proration details    (daysActive, prorateFactor, proratedCharge)
  - Customer billing type
  - Full request metadata: requestType, requestDate, processDate, status

Proration uses the ProcessDate (when the plan became active) as the
activation anchor date — matching what actually appears in the MyAdmin UI.

Date range limits:
  - GetDeviceContractAutoRequests maximum window: 60 days per call.
  - Requests wider than 60 days are split into 60-day chunks automatically.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from .auth import myadmin_call, session_store
from .customers import (
    MYADMIN_ACCOUNT,
    _sync_cache,
    _clean_name,
    _strip_han_cs,
    _strip_sub_account_suffix,
    billing_overrides,
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


def _chunk_date_range(from_dt: date, to_dt: date, max_days: int = 60):
    """
    Split [from_dt, to_dt] into consecutive chunks of at most max_days days.
    Yields (chunk_from, chunk_to) pairs.
    """
    current = from_dt
    while current <= to_dt:
        end = min(current + timedelta(days=max_days - 1), to_dt)
        yield current, end
        current = end + timedelta(days=1)


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
        billing_overrides.get(company_id)                                   # 1. UI manual override (highest priority)
        or billing_type_overrides.get(_normalize(qb_lookup_name))           # 2. name-keyed static config override
        or (qb_customers.get(_normalize(qb_lookup_name)) or {}).get("billingType")  # 3. QB record
        or ("Han-CS" if is_han_cs else "Unknown")                           # 4. {Han-CS} suffix or Unknown
    )


# --------------------------------------------------------------------------- #
#  Fetch contract request history from MyAdmin API                             #
# --------------------------------------------------------------------------- #

async def _fetch_contract_requests(
    from_dt: date,
    to_dt: date,
    account: str,
    user_company_id: str = "",
    serials: list = None,
    imeis: list = None,
) -> List[dict]:
    """
    Call GetDeviceContractAutoRequests for the date range.
    Automatically splits ranges wider than 60 days into 60-day chunks.
    Returns raw API result list.
    """
    all_results: List[dict] = []

    for chunk_from, chunk_to in _chunk_date_range(from_dt, to_dt, max_days=60):
        params: dict = {
            "apiKey":      session_store["user_id"],
            "sessionId":   session_store["session_id"],
            "forAccount":  account,
            "fromDate":    chunk_from.isoformat(),
            "toDate":      chunk_to.isoformat(),
        }
        if user_company_id:
            params["userCompanyIdFilter"] = user_company_id
        if serials:
            params["serialNos"] = serials
        if imeis:
            params["imeis"] = imeis

        response = await myadmin_call(
            "GetDeviceContractAutoRequests",
            params,
            timeout=120.0,
        )
        # Debug: log raw response shape so we can diagnose empty results
        raw_result = response.get("result")
        raw_error  = response.get("error")
        batch = raw_result or []

        # Surface API-level errors (e.g. invalid session, permission denied)
        if raw_error:
            err_msg = (raw_error.get("message") if isinstance(raw_error, dict) else str(raw_error))
            print(f"[activations] API error from GetDeviceContractAutoRequests "
                  f"{chunk_from}→{chunk_to}: {raw_error!r}")
            raise RuntimeError(f"MyAdmin returned error: {err_msg}")

        print(f"[activations] GetDeviceContractAutoRequests "
              f"{chunk_from}→{chunk_to}: "
              f"{len(batch)} records | "
              f"response_keys={list(response.keys())}")
        if batch and len(batch) > 0:
            # Log the first record's keys so we know the field names
            print(f"[activations] First record keys: {list(batch[0].keys())}")
        all_results.extend(batch)

    return all_results


# --------------------------------------------------------------------------- #
#  Enrich a single contract request into an activation row                     #
# --------------------------------------------------------------------------- #

def _enrich_request(
    req: dict,
    catalog_index: dict,
    ovr_index: dict,
    mapping_index: dict,
    cust_map_index: dict,
    full_path_index: dict,
    sku_desc_index: dict,
    category_index: dict,
    plan_promo_index: dict,
) -> Optional[dict]:
    """
    Convert a raw ApiDeviceContractAutoRequest into an Activations row.

    The AppliedDeviceContract subobject holds the resulting contract after
    the request was processed — that's where we get plan, billing dates, etc.

    Returns None if the record cannot be enriched (missing device info, etc).
    """
    # ── Device identity ───────────────────────────────────────────────────
    device   = req.get("device") or {}
    serial   = (device.get("serialNumber") or "").strip()
    imei     = str(device.get("id") or device.get("imei") or "")

    if not serial and not imei:
        return None

    # ── Applied contract (the resulting state after the request) ──────────
    adc = req.get("appliedDeviceContract") or {}

    # ── Customer (from AppliedDeviceContract.userContact, fallback to request) ──
    uc           = adc.get("userContact") or req.get("requestUser") or {}
    company      = (uc.get("userCompany") or {}) if isinstance(uc, dict) else {}
    company_id   = str(company.get("id") or "")
    company_name = (company.get("name") or "").strip()

    # ── Request metadata ──────────────────────────────────────────────────
    request_date  = _safe_date_str(req.get("requestDate"))
    process_date  = _safe_date_str(req.get("processDate"))
    status        = req.get("status") or ""
    # RequestInfo object — contains type/description of the request
    request_info  = req.get("requestInfo") or {}
    if isinstance(request_info, dict):
        request_type = (request_info.get("name") or request_info.get("description") or "")
    else:
        request_type = str(request_info)

    comments      = req.get("comments") or ""
    error_msg     = req.get("error") or ""
    rate_code     = req.get("rateCode") or ""

    # ── Plan (from applied contract) ──────────────────────────────────────
    adp          = adc.get("activeDevicePlan") or {}
    active_plan  = adp.get("name") or ""
    rate_plan    = (adc.get("promoCode") or rate_code or "").upper()
    billing_plan = active_plan.split(":")[0].strip() if active_plan else ""

    # Device plan from request-level field (fallback)
    if not active_plan:
        dp = req.get("devicePlan") or {}
        active_plan = dp.get("name") or ""
        billing_plan = active_plan.split(":")[0].strip() if active_plan else ""

    # ── Dates ─────────────────────────────────────────────────────────────
    # Prefer override → applied contract dates → request-level dates
    serial_key   = serial.upper()
    fcd_override = first_connect_date_overrides.get(serial_key)
    bsd_override = billing_date_overrides.get(serial_key)

    raw_fcd = fcd_override or _safe_date_str(adc.get("firstDeviceActivationDate"))
    raw_bsd = bsd_override or _safe_date_str(adc.get("billingStartDate"))
    raw_start = _safe_date_str(adc.get("startDate"))
    raw_end   = _safe_date_str(adc.get("endDate"))

    # ── Activation date = ProcessDate (authoritative event date from MyAdmin) ──
    # Fall back to RequestDate, then to firstDeviceActivationDate, then billingStartDate.
    activation_date_str = process_date or request_date or raw_fcd or raw_bsd
    if not activation_date_str:
        return None
    activation_date_obj = _parse_date(activation_date_str)
    if not activation_date_obj:
        return None

    # ── Auto-activated flag ───────────────────────────────────────────────
    # IsAutoActivated comes directly from the applied contract
    is_auto_activated_api = adc.get("isAutoActivated")
    if is_auto_activated_api is True:
        auto_activated = True
    elif raw_fcd and raw_bsd:
        fcd_obj = _parse_date(raw_fcd)
        bsd_obj = _parse_date(raw_bsd)
        auto_activated = bool(fcd_obj and bsd_obj and bsd_obj < fcd_obj)
    else:
        auto_activated = False

    # ── Active database ───────────────────────────────────────────────────
    ldd = adc.get("latestDeviceDatabase") or {}
    active_db = ldd.get("databaseName") or ""

    # ── Billing type ──────────────────────────────────────────────────────
    billing_type = _get_billing_type(company_id, company_name)

    # ── SKU resolution ────────────────────────────────────────────────────
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

    sku_category      = category_index.get(sku_key, "")
    excluded_category = sku_category in EXCLUDED_CATEGORIES
    item_code         = full_path_index.get(sku_key, sku_key)
    sku_desc          = sku_desc_index.get(sku_key, sku_key)

    # ── Proration ─────────────────────────────────────────────────────────
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
        "serialNumber":      serial,
        "imei":              imei,

        # Customer
        "companyId":         company_id,
        "customerName":      company_name,
        "activeDatabase":    active_db,
        "billingType":       billing_type,

        # Plan info
        "activePlan":        active_plan,
        "ratePlanCode":      rate_plan,
        "isPilot":           is_pilot,
        "autoActivated":     auto_activated,

        # Request metadata (new — from contract request history)
        "requestType":       request_type,
        "requestDate":       request_date,
        "processDate":       process_date,
        "status":            status,
        "comments":          comments,
        "errorMessage":      error_msg,

        # Dates
        "firstConnectDate":    raw_fcd,
        "billingStartDate":    raw_bsd,
        "contractStartDate":   raw_start,
        "contractEndDate":     raw_end,
        "activationDate":      activation_date_str,   # = processDate (authoritative)

        # SKU
        "skuKey":            sku_key,
        "itemCode":          item_code,
        "skuDesc":           sku_desc,
        "skuCategory":       sku_category,
        "excludedCategory":  excluded_category,

        # Proration
        "proration":         proration,
    }


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
    request_type: str = Query(
        default="",
        description="Filter by request type (e.g. 'Activate', 'Plan Change'). Empty = all.",
        alias="requestType",
    ),
):
    """
    Returns devices that had a contract request (activation or plan change)
    within the requested date range, sourced directly from MyAdmin's
    Device Contract Request History (GetDeviceContractAutoRequests).

    Each record is enriched with SKU resolution and proration details.

    Date defaults: first day of the current month → today.
    Maximum single-call range: 60 days (auto-chunked for wider ranges).
    """
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in to MyAdmin")

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

    # Fetch raw contract request history from MyAdmin
    print(f"[activations] Fetching contract requests: "
          f"{from_date} to {to_date} | account={MYADMIN_ACCOUNT} | "
          f"session_id={'SET' if session_store.get('session_id') else 'MISSING'} | "
          f"user_id={'SET' if session_store.get('user_id') else 'MISSING'}")
    try:
        raw_requests = await _fetch_contract_requests(
            from_dt=from_dt,
            to_dt=to_dt,
            account=MYADMIN_ACCOUNT,
            user_company_id=customer_id,
        )
    except Exception as exc:
        print(f"[activations] ERROR from _fetch_contract_requests: {exc}")
        raise HTTPException(
            status_code=502,
            detail=f"MyAdmin API error fetching contract requests: {exc}",
        )

    # Build SKU indices (from local config files — fast, no API call)
    try:
        (catalog_index, ovr_index, mapping_index,
         cust_map_index, full_path_index, sku_desc_index,
         category_index, plan_promo_index) = _build_indices()
    except Exception as exc:
        import traceback
        print(f"[activations] ERROR in _build_indices: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to load SKU indices: {exc}")

    results: List[dict] = []

    for req in raw_requests:
        try:
            row = _enrich_request(
                req,
                catalog_index=catalog_index,
                ovr_index=ovr_index,
                mapping_index=mapping_index,
                cust_map_index=cust_map_index,
                full_path_index=full_path_index,
                sku_desc_index=sku_desc_index,
                category_index=category_index,
                plan_promo_index=plan_promo_index,
            )
        except Exception as enrich_exc:
            import traceback
            print(f"[activations] _enrich_request error on req={req.get('device',{}).get('serialNumber','?')}: "
                  f"{enrich_exc}\n{traceback.format_exc()}")
            continue

        if row is None:
            continue

        # Billing type filter
        if billing_type and row["billingType"] != billing_type:
            continue

        # Request type filter
        if request_type and request_type.lower() not in (row["requestType"] or "").lower():
            continue

        results.append(row)

    # Sort: by processDate / activationDate ascending
    results.sort(key=lambda r: r.get("activationDate") or "")

    # Summary stats
    total          = len(results)
    total_prorated = round(sum(
        (r["proration"] or {}).get("proratedCharge", 0.0)
        for r in results if r.get("proration")
    ), 2)
    unmapped_cnt   = sum(1 for r in results if r["skuKey"] == "UNMAPPED")
    excluded_cnt   = sum(1 for r in results if r["excludedCategory"])
    pilot_cnt      = sum(1 for r in results if r.get("isPilot"))
    auto_cnt       = sum(1 for r in results if r.get("autoActivated"))

    # Cache age info (still shown for reference — contracts cache used for billing type only)
    cache_fetched_at = _sync_cache.get("fetched_at")
    cache_age_hours  = (
        round((__import__("time").time() - cache_fetched_at) / 3600, 1)
        if cache_fetched_at else None
    )

    return {
        "fromDate":              from_date,
        "toDate":                to_date,
        "totalRecords":          total,
        "totalProratedAmount":   total_prorated,
        "unmappedCount":         unmapped_cnt,
        "excludedCount":         excluded_cnt,
        "pilotCount":            pilot_cnt,
        "autoActivatedCount":    auto_cnt,
        "rawRequestCount":       len(raw_requests),
        "cacheAgeHours":         cache_age_hours,
        "records":               results,
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
