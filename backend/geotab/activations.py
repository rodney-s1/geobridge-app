"""
activations.py — Activations tab backend
=========================================

Provides the /api/activations endpoint that fetches Device Contract Request
History from MyAdmin (the "Activation History" page) and enriches each record
with:
  - Resolved QB SKU key (same tier logic as invoices.py / reconciliation.py)
  - Prorated charge calculation (same engine as _generate_prorated_invoice)
  - Billing type for the customer
  - Links to proration preview

This becomes the source of truth for:
  1. Generating prorated invoices (feeds the existing invoices.py proration engine)
  2. Updating QB Recurrences (future QB sync tool will consume this data)

MyAdmin API method: GetDeviceContractRequestsByPage
  - Accepts: apiKey, sessionId, forAccount, fromDate, toDate, nextId
  - Returns paginated list of DeviceContractRequest objects with fields:
      device.serialNumber, device.imei, sim, account
      activeDatabase / activeCustomer
      requestedPlan, requestType, requestedOn, processedOn
      activeFeatures, status, comments
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
    billing_type_overrides,
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
    DM_SERIAL_PREFIXES,
)
from .reconciliation import _normalize, _resolve_price

router = APIRouter()

# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _safe_date_str(raw) -> str:
    """Trim ISO datetime to YYYY-MM-DD; return '' for sentinel 0001-01-01."""
    s = (raw or "")[:10]
    return "" if s.startswith("0001") else s


def _parse_date(s: str) -> Optional[date]:
    """Parse YYYY-MM-DD string to date, return None on failure."""
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _days_in_month(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]


# --------------------------------------------------------------------------- #
#  Request type helpers                                                         #
# --------------------------------------------------------------------------- #

# Request types that represent a new device coming online (activation).
# All other types (Terminate, Plan Change, etc.) are non-activation events.
ACTIVATION_REQUEST_TYPES: frozenset = frozenset({
    "activate",
    "new activation",
    "new device",
    "add device",
    "device activation",
    "activated",
    "activation",
})


def _is_activation(request_type: str) -> bool:
    """Return True if this request type represents a new activation."""
    rt = (request_type or "").strip().lower()
    # Check for exact match or substring match
    for at in ACTIVATION_REQUEST_TYPES:
        if at in rt or rt in at:
            return True
    return False


# --------------------------------------------------------------------------- #
#  SKU resolution for activation records                                        #
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
    """
    Resolve the QB SKU for an activation record using the same tier logic
    as invoices.py _generate_prorated_invoice():
      0.5: Serial-prefix OEM / Surfsight check
      1.5: GE/GF with no promoCode → base hardware SKU
      1:   Customer-specific promoCode mapping
      1.5: Plan+promoCode compound lookup (Tier 1.5)
      3:   Global flat promoCode mapping
      4:   Billing plan fallback
      ?: UNMAPPED
    """
    serial_upper = (serial or "").strip().upper()
    _gf_serial   = serial_upper.startswith("GF")
    _ge_serial   = serial_upper.startswith("GE")

    # Step 0.5: DM devices are excluded (never on prorated invoices)
    if _is_dm_serial(serial):
        return "EXCLUDED (Digital Matter)"

    # Step 1.5: GE/GF with no promoCode → base hardware SKU directly
    if not rate_plan_code and (_gf_serial or _ge_serial):
        return _GE_GF_BASE_SKU["GF" if _gf_serial else "GE"]

    # Step 1: Serial-prefix check (OEM + Surfsight)
    sku = _sku_from_serial(serial)

    # Step 2-4: Plan/promo resolution
    if not sku:
        sku = (
            _resolve_sku(customer_norm, rate_plan_code, mapping_index, cust_map_index,
                         billing_plan, plan_promo_index)
            or _resolve_sku(customer_norm, billing_plan, mapping_index, cust_map_index)
            or "UNMAPPED"
        )

    # GF serial post-correction: remap Focus Plus → Focus
    if _gf_serial and sku in _GF_SKU_REMAP:
        sku = _GF_SKU_REMAP[sku]

    return sku


# --------------------------------------------------------------------------- #
#  Customer billing-type lookup (same logic as invoices.py)                    #
# --------------------------------------------------------------------------- #

def _get_billing_type(company_id: str, raw_name: str) -> str:
    """
    Derive billing type for a company using the same priority chain as
    invoices.py get_prorated_invoices():
      1. Manual override (billing_type_overrides)
      2. QB record billingType (via normalized suffix-stripped name)
      3. {Han-CS} suffix in MyAdmin name
      4. "Unknown"
    """
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
#  Fetch from MyAdmin                                                           #
# --------------------------------------------------------------------------- #

async def _fetch_activation_history(
    from_date: str,
    to_date: str,
    include_all_types: bool = False,
) -> List[dict]:
    """
    Fetch Device Contract Request History from MyAdmin.
    Uses GetDeviceContractRequestsByPage with date range filter.

    Returns a flat list of raw request records.
    Paginates automatically (1000 records per page).
    """
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in to MyAdmin")

    all_records: List[dict] = []
    next_id = 0
    page_num = 0

    while True:
        page_num += 1
        params: dict = {
            "apiKey":     session_store["user_id"],
            "sessionId":  session_store["session_id"],
            "forAccount": MYADMIN_ACCOUNT,
            "nextId":     next_id,
        }

        # Add date range if provided
        if from_date:
            params["fromDate"] = from_date + "T00:00:00"
        if to_date:
            params["toDate"] = to_date + "T23:59:59"

        try:
            result = await myadmin_call(
                "GetDeviceContractRequestsByPage",
                params,
                timeout=120.0,
            )
        except Exception as exc:
            # If the method doesn't exist or returns an API error, surface it clearly
            raise HTTPException(
                status_code=502,
                detail=f"MyAdmin API error fetching activation history (page {page_num}): {exc}",
            )

        batch = result.get("result") or []

        if not batch:
            break

        all_records.extend(batch)

        # Stop if last page (< 1000 records)
        if len(batch) < 1000:
            break

        # Advance cursor
        next_id = batch[-1].get("id") or batch[-1].get("Id") or 0
        if not next_id:
            break

    return all_records


# --------------------------------------------------------------------------- #
#  Enrich a single activation record                                            #
# --------------------------------------------------------------------------- #

def _enrich_record(
    rec: dict,
    catalog_index: dict,
    ovr_index: dict,
    mapping_index: dict,
    cust_map_index: dict,
    full_path_index: dict,
    sku_desc_index: dict,
    category_index: dict,
    plan_promo_index: dict,
) -> dict:
    """
    Enrich a raw MyAdmin contract request record with:
      - Normalised field names
      - Resolved SKU key + billing metadata
      - Proration details (if activation date is in a complete billing month)
      - Customer billing type
    """
    # --- Device fields ---
    device = rec.get("device") or {}
    serial    = device.get("serialNumber") or ""
    imei      = device.get("imei") or str(device.get("id") or "")

    # --- SIM ---
    sim_obj = rec.get("sim") or {}
    sim = sim_obj.get("serialNumber") or sim_obj.get("number") or ""

    # --- Account / Customer ---
    user_contact = rec.get("userContact") or {}
    user_company = user_contact.get("userCompany") or {}
    company_id   = str(user_company.get("id") or "")
    company_name = user_company.get("name") or ""

    # Active Database
    active_db = ""
    ldd = rec.get("latestDeviceDatabase") or rec.get("activeDatabase") or {}
    if isinstance(ldd, dict):
        active_db = ldd.get("databaseName") or ldd.get("name") or ""
    elif isinstance(ldd, str):
        active_db = ldd

    # --- Plan / Request info ---
    # The requested plan is in requestedDevicePlan or similar
    requested_plan_obj = rec.get("requestedDevicePlan") or rec.get("newDevicePlan") or {}
    requested_plan = ""
    if isinstance(requested_plan_obj, dict):
        requested_plan = requested_plan_obj.get("name") or ""
    elif isinstance(requested_plan_obj, str):
        requested_plan = requested_plan_obj

    # Active/current plan
    active_plan_obj = rec.get("activeDevicePlan") or {}
    if isinstance(active_plan_obj, dict):
        active_plan = active_plan_obj.get("name") or ""
    else:
        active_plan = str(active_plan_obj) if active_plan_obj else ""

    # Rate plan code (promoCode in MyAdmin)
    rate_plan_code = (rec.get("promoCode") or rec.get("ratePlanCode") or "").upper()

    # Request type (e.g. "Activate", "Terminate", "Mo Plan Change")
    request_type_raw = rec.get("requestType") or rec.get("type") or ""
    if isinstance(request_type_raw, dict):
        request_type = request_type_raw.get("name") or str(request_type_raw)
    else:
        request_type = str(request_type_raw)

    is_activation_event = _is_activation(request_type)

    # Status
    status_raw = rec.get("status") or {}
    if isinstance(status_raw, dict):
        status = status_raw.get("name") or str(status_raw)
    else:
        status = str(status_raw)

    # Comments
    comments = rec.get("comments") or ""

    # Active features
    active_features_raw = rec.get("activeFeatures") or []
    if isinstance(active_features_raw, list):
        active_features = ", ".join(
            f.get("name") if isinstance(f, dict) else str(f)
            for f in active_features_raw
        )
    else:
        active_features = str(active_features_raw)

    # --- Dates ---
    requested_on   = _safe_date_str(rec.get("requestedOn") or rec.get("createdOn"))
    processed_on   = _safe_date_str(rec.get("processedOn") or rec.get("completedOn"))
    first_connect  = _safe_date_str(rec.get("firstDeviceActivationDate"))
    billing_start  = _safe_date_str(rec.get("billingStartDate"))

    # Use the best available activation date for proration
    activation_date_str = first_connect or billing_start or processed_on or requested_on
    activation_date_obj = _parse_date(activation_date_str)

    # --- Billing type ---
    billing_type = _get_billing_type(company_id, company_name)

    # Determine billing plan (strip ": Live" suffix same as reconciliation)
    billing_plan_full = active_plan or requested_plan
    billing_plan = billing_plan_full.split(":")[0].strip() if billing_plan_full else ""

    # --- SKU resolution ---
    customer_norm = _normalize(company_name)
    sku_key = _resolve_activation_sku(
        serial=serial,
        customer_norm=customer_norm,
        rate_plan_code=rate_plan_code,
        billing_plan=billing_plan,
        mapping_index=mapping_index,
        cust_map_index=cust_map_index,
        plan_promo_index=plan_promo_index,
    )

    # Category check
    sku_category = category_index.get(sku_key, "")
    excluded_category = sku_category in EXCLUDED_CATEGORIES

    # Full QB item path and description
    item_code = full_path_index.get(sku_key, sku_key)
    sku_desc  = sku_desc_index.get(sku_key, sku_key)

    # --- Proration ---
    proration = None
    if activation_date_obj and not excluded_category and sku_key not in ("UNMAPPED", "EXCLUDED (Digital Matter)"):
        b_year  = activation_date_obj.year
        b_month = activation_date_obj.month
        monthly_rate, price_source = _resolve_price(
            customer_norm, sku_key, ovr_index, catalog_index
        )
        if monthly_rate:
            days_active, days_in_month, factor = _prorate_factor(
                activation_date_obj, b_year, b_month
            )
            prorated_charge = round(monthly_rate * factor, 2)
            proration = {
                "billingMonth":    f"{b_year}-{b_month:02d}",
                "activationDate":  activation_date_str,
                "daysActive":      days_active,
                "daysInMonth":     days_in_month,
                "prorateFactor":   round(factor, 6),
                "monthlyRate":     monthly_rate,
                "proratedCharge":  prorated_charge,
                "priceSource":     price_source,
            }

    return {
        # Identity
        "id":               rec.get("id") or rec.get("Id") or "",
        "serialNumber":     serial,
        "imei":             imei,
        "sim":              sim,

        # Customer
        "companyId":        company_id,
        "customerName":     company_name,
        "activeDatabase":   active_db,
        "billingType":      billing_type,

        # Plan / Request
        "requestedPlan":    requested_plan,
        "activePlan":       active_plan,
        "ratePlanCode":     rate_plan_code,
        "requestType":      request_type,
        "isActivation":     is_activation_event,
        "status":           status,
        "activeFeatures":   active_features,
        "comments":         comments,

        # Dates
        "requestedOn":      requested_on,
        "processedOn":      processed_on,
        "firstConnectDate": first_connect,
        "billingStartDate": billing_start,
        "activationDate":   activation_date_str,

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
#  Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("/activations")
async def get_activations(
    from_date: str = Query(
        default="",
        description="Start date YYYY-MM-DD (defaults to 30 days ago)",
        alias="fromDate",
    ),
    to_date: str = Query(
        default="",
        description="End date YYYY-MM-DD (defaults to today)",
        alias="toDate",
    ),
    request_type: str = Query(
        default="",
        description="Filter by request type substring (e.g. 'activate', 'terminate'). "
                    "Empty = all types.",
        alias="requestType",
    ),
    activations_only: bool = Query(
        default=False,
        description="When true, return only records that represent new activations.",
        alias="activationsOnly",
    ),
    customer_id: str = Query(
        default="",
        description="Filter to a specific MyAdmin company ID.",
        alias="customerId",
    ),
):
    """
    Fetch Device Contract Request History from MyAdmin (Activation History page).

    Returns a list of enriched activation records with:
      - Device / customer / plan / request metadata
      - Resolved QB SKU key
      - Proration details (activationDate, daysActive, proratedCharge, etc.)
      - Customer billing type

    Date defaults: last 30 days.
    """
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in to MyAdmin")

    # Default date range: last 30 days
    today = date.today()
    if not from_date:
        from_date = (today - timedelta(days=30)).isoformat()
    if not to_date:
        to_date = today.isoformat()

    # Validate dates
    from_dt = _parse_date(from_date)
    to_dt   = _parse_date(to_date)
    if not from_dt or not to_dt:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")
    if from_dt > to_dt:
        raise HTTPException(status_code=400, detail="fromDate must be <= toDate")

    # Fetch raw records from MyAdmin
    raw_records = await _fetch_activation_history(from_date, to_date)

    # Build SKU resolution indices (same as invoices.py)
    (catalog_index, ovr_index, mapping_index,
     cust_map_index, full_path_index, sku_desc_index,
     category_index, plan_promo_index) = _build_indices()

    # Enrich each record
    enriched: List[dict] = []
    for rec in raw_records:
        try:
            row = _enrich_record(
                rec,
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
            # Don't let one bad record abort the whole response
            continue

        # Apply filters
        if activations_only and not row["isActivation"]:
            continue
        if request_type and request_type.lower() not in row["requestType"].lower():
            continue
        if customer_id and row["companyId"] != customer_id:
            continue

        enriched.append(row)

    # Sort: most recent first (by requestedOn desc)
    enriched.sort(key=lambda r: r.get("requestedOn") or "", reverse=True)

    # Summary stats
    total           = len(enriched)
    activation_cnt  = sum(1 for r in enriched if r["isActivation"])
    total_prorated  = sum(
        (r["proration"] or {}).get("proratedCharge", 0.0)
        for r in enriched
        if r.get("proration")
    )
    unmapped_cnt    = sum(1 for r in enriched if r["skuKey"] == "UNMAPPED")

    return {
        "fromDate":        from_date,
        "toDate":          to_date,
        "totalRecords":    total,
        "activationCount": activation_cnt,
        "totalProratedAmount": round(total_prorated, 2),
        "unmappedCount":   unmapped_cnt,
        "records":         enriched,
    }


@router.get("/activations/summary")
async def get_activations_summary(
    from_date: str = Query(default="", alias="fromDate"),
    to_date:   str = Query(default="", alias="toDate"),
):
    """
    Lightweight summary of activation counts and prorated amounts
    grouped by customer and billing month. Useful for dashboard widgets.
    """
    result = await get_activations(
        from_date=from_date,
        to_date=to_date,
        activations_only=True,
    )

    by_customer: Dict[str, dict] = defaultdict(lambda: {
        "customerName": "",
        "billingType":  "",
        "count":        0,
        "totalProrated": 0.0,
        "skus": defaultdict(int),
    })

    for r in result["records"]:
        cid = r["companyId"] or r["customerName"]
        by_customer[cid]["customerName"]  = r["customerName"]
        by_customer[cid]["billingType"]   = r["billingType"]
        by_customer[cid]["count"]        += 1
        if r.get("proration"):
            by_customer[cid]["totalProrated"] += r["proration"].get("proratedCharge", 0.0)
        by_customer[cid]["skus"][r["skuKey"]] = (
            by_customer[cid]["skus"].get(r["skuKey"], 0) + 1
        )

    # Convert defaultdict(int) inside skus to plain dict
    summary_list = []
    for cid, data in by_customer.items():
        summary_list.append({
            "companyId":     cid,
            "customerName":  data["customerName"],
            "billingType":   data["billingType"],
            "activationCount": data["count"],
            "totalProrated": round(data["totalProrated"], 2),
            "skus":          dict(data["skus"]),
        })

    summary_list.sort(key=lambda x: x["activationCount"], reverse=True)

    return {
        "fromDate":  result["fromDate"],
        "toDate":    result["toDate"],
        "customers": summary_list,
    }
