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

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import myadmin_call, require_session, session_store
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

router = APIRouter(dependencies=[Depends(require_session)])

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
            # MyAdmin requires full ISO 8601 UTC datetime strings.
            # Date-only strings may miss events; cover the full calendar day.
            "fromDate":    chunk_from.strftime("%Y-%m-%dT00:00:00Z"),
            "toDate":      chunk_to.strftime("%Y-%m-%dT23:59:59Z"),
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

    # ── Top-level active billing plan (current device state) ──────────────
    # req["activeDevicePlan"] is the device's LIVE billing plan as of now —
    # distinct from appliedDeviceContract.activeDevicePlan which only reflects
    # the plan assigned by this specific request.  A terminated device will
    # show "Terminated" here even if its last request event said "Pro Mode".
    top_adp            = req.get("activeDevicePlan") or {}
    top_active_plan    = (top_adp.get("name") or "").strip()
    # If the device's current billing plan contains any exclusion term, bail out.
    if any(t in top_active_plan.lower() for t in ("terminat", "cancel", "deactivat", "suspend", "remove")):
        return None

    # ── Dates ─────────────────────────────────────────────────────────────
    # Prefer override → applied contract dates → request-level dates
    serial_key   = serial.upper()
    fcd_override = first_connect_date_overrides.get(serial_key)
    bsd_override = billing_date_overrides.get(serial_key)

    raw_fcd = fcd_override or _safe_date_str(adc.get("firstDeviceActivationDate"))
    raw_bsd = bsd_override or _safe_date_str(adc.get("billingStartDate"))
    raw_start = _safe_date_str(adc.get("startDate"))
    raw_end   = _safe_date_str(adc.get("endDate"))

    # ── Activation date ────────────────────────────────────────────────────
    # Priority (per-device dates beat the bulk request event date):
    #
    #   1. Manual override  (highest priority)
    #   2. firstDeviceActivationDate — actual day the device first connected
    #   3. billingStartDate          — billing anchor (used for auto-activated)
    #   4. processDate               — when the contract request was processed
    #                                  (batch requests all share one processDate;
    #                                   using this as primary collapses all devices
    #                                   in a bulk request onto the same date — wrong)
    #   5. requestDate               — last resort
    #
    # Note: for auto-activated devices, fcd may be absent or in the future;
    # billingStartDate is the correct anchor in that case (see auto_activated logic below).
    activation_date_str = (
        raw_fcd                 # firstDeviceActivationDate (or manual override)
        or raw_bsd              # billingStartDate (or manual override)
        or process_date         # batch processDate — fallback only
        or request_date         # last resort
    )
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

    # For auto-activated devices, billingStartDate is the correct activation
    # anchor (billing begins before the device physically connects).
    # Re-apply here after we know auto_activated, overriding the fcd-first priority.
    if auto_activated and raw_bsd:
        activation_date_str = raw_bsd
        activation_date_obj = _parse_date(raw_bsd) or activation_date_obj

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
        "topActivePlan":     top_active_plan,   # device's current live billing plan
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

    _EXCLUSION_TERMS = ("terminat", "cancel", "deactivat", "suspend", "remove")
    results: List[dict] = []

    # Build a serial → isTerminated lookup from the contracts cache.
    # GetDeviceContractAutoRequests does NOT reliably return current termination
    # status — the activeDevicePlan field on those records reflects the plan at
    # request time, not the device's current live billing state.  The contracts
    # cache (populated from GetContracts) does include isTerminated:True for
    # terminated devices.  Cross-referencing here catches cases where a device
    # has a contract request event in the window but is currently terminated
    # (e.g. terminated 2026-05-15, then an auto-activation event fires anyway).
    _terminated_serials: set = set()
    for _c in (_sync_cache.get("contracts") or []):
        if _c.get("isTerminated"):
            _dev = _c.get("device") or {}
            _sn  = (_dev.get("serialNumber") or "").strip().upper()
            if _sn:
                _terminated_serials.add(_sn)

    _DEBUG_SERIALS = {"G4HBP90KY1VA", "GAXD467UPNKH"}

    for req in raw_requests:
        # Temporary debug: dump full raw record for known problem serials
        _dbg_serial = ((req.get("device") or {}).get("serialNumber") or "").upper()
        if _dbg_serial in _DEBUG_SERIALS:
            import json as _json
            print(f"[activations DEBUG] RAW RECORD for {_dbg_serial}:")
            print(_json.dumps(req, default=str, indent=2)[:4000])

        # Contracts-cache termination check: if the device is marked isTerminated
        # in our GetContracts cache, skip it unconditionally — regardless of what
        # GetDeviceContractAutoRequests says about plan/request type.
        _req_serial_upper = ((req.get("device") or {}).get("serialNumber") or "").strip().upper()
        if _req_serial_upper and _req_serial_upper in _terminated_serials:
            continue

        # Pre-enrichment fast-path: skip obvious termination records by checking
        # the raw requestInfo name and devicePlan name before full enrichment.
        _raw_req_info = req.get("requestInfo") or {}
        _raw_req_type = (
            (_raw_req_info.get("name") or _raw_req_info.get("description") or "")
            if isinstance(_raw_req_info, dict) else str(_raw_req_info)
        ).lower()
        _raw_plan = ((req.get("devicePlan") or {}).get("name") or "").lower()
        _raw_adc_plan = (((req.get("appliedDeviceContract") or {}).get("activeDevicePlan") or {}).get("name") or "").lower()
        # Top-level activeDevicePlan on the contract row reflects the device's
        # CURRENT live billing status (e.g. "Terminated") — distinct from the
        # appliedDeviceContract.activeDevicePlan which shows the plan for this
        # specific request event and may still say "Pro Mode" even on a
        # terminated device.
        _raw_top_plan = ((req.get("activeDevicePlan") or {}).get("name") or "").lower()
        if any(t in _raw_req_type  for t in _EXCLUSION_TERMS) or \
           any(t in _raw_plan      for t in _EXCLUSION_TERMS) or \
           any(t in _raw_adc_plan  for t in _EXCLUSION_TERMS) or \
           any(t in _raw_top_plan  for t in _EXCLUSION_TERMS):
            continue

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

        # Date range filter: only keep records whose per-device activationDate
        # falls within the requested window.
        # (The API returns all requests processed in the window, but a bulk
        # request may include devices whose individual activation dates predate
        # the window — those should not appear.)
        row_date = _parse_date(row.get("activationDate") or "")
        if row_date and not (from_dt <= row_date <= to_dt):
            continue

        # Contract end-date filter: if the device's contract ended BEFORE the
        # start of the queried window, it was already terminated/inactive when
        # the window opened and should not appear as a new activation.
        #
        # Example: device terminated 2026-05-15, query window 2026-07-01.
        # The activation request event still exists in the API response (it was
        # processed inside the 60-day lookback window), but the contract is
        # long dead — exclude it.
        #
        # We only exclude when endDate < from_dt (strictly before window start).
        # If endDate is within the window or after it, the device was active
        # for at least part of the queried period and should remain.
        _contract_end = _parse_date(row.get("contractEndDate") or "")
        if _contract_end and _contract_end < from_dt:
            continue

        # Exclude termination / cancellation events — these are not activations.
        # Check requestType, activePlan, AND status since "Terminate Mode" can
        # appear in the plan name rather than the request type field.
        _req_type_lower    = (row.get("requestType") or "").lower()
        _active_plan_lower = (row.get("activePlan") or "").lower()
        _top_plan_lower    = (row.get("topActivePlan") or "").lower()
        _status_lower      = (row.get("status") or "").lower()
        if any(t in _req_type_lower    for t in _EXCLUSION_TERMS) or \
           any(t in _active_plan_lower  for t in _EXCLUSION_TERMS) or \
           any(t in _top_plan_lower     for t in _EXCLUSION_TERMS) or \
           any(t in _status_lower       for t in _EXCLUSION_TERMS):
            continue

        # Billing type filter
        if billing_type and row["billingType"] != billing_type:
            continue

        # Request type filter
        if request_type and request_type.lower() not in (row["requestType"] or "").lower():
            continue

        results.append(row)

    # Deduplicate by serialNumber: the API can return multiple request records
    # for the same device in a date range (e.g. activate + plan change, or
    # duplicate bulk-request entries). Keep only one row per serial, preferring:
    #   1. Earliest activationDate (the original activation event)
    #   2. On tie: record with a resolved SKU over UNMAPPED
    seen: dict = {}   # serial_upper -> best row so far
    for row in results:
        serial_key = (row.get("serialNumber") or "").upper()
        if not serial_key:
            continue
        if serial_key not in seen:
            seen[serial_key] = row
        else:
            existing = seen[serial_key]
            # Prefer earlier activationDate
            new_date = row.get("activationDate") or ""
            old_date = existing.get("activationDate") or ""
            if new_date < old_date:
                seen[serial_key] = row
            elif new_date == old_date:
                # Tie-break: prefer mapped SKU over UNMAPPED
                if existing.get("skuKey") == "UNMAPPED" and row.get("skuKey") != "UNMAPPED":
                    seen[serial_key] = row
    results = list(seen.values())

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


@router.get("/activations/debug-raw")
async def get_activations_debug_raw(
    from_date: str = Query(default="", alias="fromDate"),
    to_date:   str = Query(default="", alias="toDate"),
):
    """
    Diagnostic endpoint — returns the raw MyAdmin API response for
    GetDeviceContractAutoRequests without any enrichment.

    Useful for confirming what the API actually returns (field names,
    record count, error messages) before enrichment filters anything out.

    Access at: GET /api/activations/debug-raw?fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD
    """
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in to MyAdmin")

    today = date.today()
    if not from_date:
        from_date = date(today.year, today.month, 1).isoformat()
    if not to_date:
        to_date = today.isoformat()

    from_dt = _parse_date(from_date)
    to_dt   = _parse_date(to_date)
    if not from_dt or not to_dt:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")

    chunks = []
    for chunk_from, chunk_to in _chunk_date_range(from_dt, to_dt, max_days=60):
        params = {
            "apiKey":    session_store["user_id"],
            "sessionId": session_store["session_id"],
            "forAccount": MYADMIN_ACCOUNT,
            "fromDate":  chunk_from.strftime("%Y-%m-%dT00:00:00Z"),
            "toDate":    chunk_to.strftime("%Y-%m-%dT23:59:59Z"),
        }
        try:
            response = await myadmin_call("GetDeviceContractAutoRequests", params, timeout=120.0)
        except Exception as exc:
            response = {"exception": str(exc)}

        raw_result = response.get("result") or []
        first_record_keys = list(raw_result[0].keys()) if raw_result else []

        chunks.append({
            "fromDate":       params["fromDate"],
            "toDate":         params["toDate"],
            "recordCount":    len(raw_result),
            "firstRecordKeys": first_record_keys,
            "firstRecord":    raw_result[0] if raw_result else None,
            "error":          response.get("error"),
            "responseKeys":   list(response.keys()),
        })

    return {
        "account":    MYADMIN_ACCOUNT,
        "sessionSet": bool(session_store.get("session_id")),
        "chunks":     chunks,
        "totalRecords": sum(c["recordCount"] for c in chunks),
    }
