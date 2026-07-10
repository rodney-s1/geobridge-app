"""
GeoBridge Reports API
=====================
Read-only analytics derived from the MyAdmin contracts cache and
reconciliation indices.  Nothing here syncs to QuickBooks.

Endpoints
---------
GET /api/reports/summary          — full data bundle for all 7 report tabs
GET /api/reports/terminated        — terminated devices with month-over-month trend
"""
from __future__ import annotations

import html as _html
import os
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_session

router = APIRouter(dependencies=[Depends(require_session)])

_HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_date(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _month_label(ym: str) -> str:
    """'2026-07' → 'Jul 2026'"""
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return ym


def _normalize(s: str) -> str:
    s = (s or "").strip()
    pipe = s.find(" | ")
    if pipe != -1:
        s = s[:pipe].strip()
    first_open = s.find("{")
    if first_open == -1:
        return s.lower()
    first_close = s.find("}", first_open)
    first_token = s[first_open + 1: first_close].strip() if first_close != -1 else ""
    if first_token.lower() == "han-cs":
        return s[:first_close + 1].strip().lower()
    return s[:first_open].strip().lower()


def _customer_name(contract: dict) -> str:
    uc = contract.get("userContact") or {}
    co = uc.get("userCompany") or {}
    return _html.unescape(co.get("name") or "").strip()


def _company_id(contract: dict) -> str:
    uc = contract.get("userContact") or {}
    co = uc.get("userCompany") or {}
    return str(co.get("id") or "")


def _serial(contract: dict) -> str:
    dev = contract.get("device") or {}
    return (dev.get("serialNumber") or "").strip()


def _load(path, default):
    import json
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, Exception):
        return default


# ---------------------------------------------------------------------------
# Price resolution (same logic as reconciliation._resolve_price)
# ---------------------------------------------------------------------------

def _build_price_index() -> Tuple[dict, dict]:
    """Returns (ovr_index, catalog_index)."""
    catalog   = _load(os.path.join(_HERE, "sku_catalog.json"), [])
    overrides = _load(os.path.join(_HERE, "sku_customer_overrides.json"), [])
    catalog_index = {s["skuKey"]: float(s.get("defaultPrice") or 0) for s in catalog}
    ovr_index = {
        (_normalize(o["customerName"]), o["skuKey"]): float(o.get("price") or 0)
        for o in overrides
    }
    return ovr_index, catalog_index


def _resolve_monthly_rate(customer_name: str, sku_key: str,
                           ovr_index: dict, catalog_index: dict) -> float:
    """
    For dash-department sub-accounts the parent override takes priority over
    any QB-imported entry stored under the full sub-account name.
    """
    norm = _normalize(customer_name)
    # Parent-name check first (covers "City of Raleigh - Solid Waste" → "city of raleigh")
    dash = norm.find(" - ")
    if dash != -1:
        parent_key = (norm[:dash].strip(), sku_key)
        if parent_key in ovr_index:
            return ovr_index[parent_key]
    # Exact match (non-dash names, or sub-accounts with their own configured price)
    key = (norm, sku_key)
    if key in ovr_index:
        return ovr_index[key]
    return catalog_index.get(sku_key) or 0.0


# ---------------------------------------------------------------------------
# SKU resolution (simplified — promo-code → sku_key → monthly rate)
# ---------------------------------------------------------------------------

def _build_sku_index() -> Tuple[dict, dict]:
    """Returns (mapping_index, cust_map_index)."""
    mappings  = _load(os.path.join(_HERE, "sku_mappings.json"), [])
    cust_maps = _load(os.path.join(_HERE, "customer_rate_plan_mappings.json"), [])
    mapping_index: dict = {}
    for m in mappings:
        key = (m.get("ratePlanCode") or m.get("promoCode") or "").upper()
        if key and key not in mapping_index:
            mapping_index[key] = m.get("skuKey") or ""
    cust_map_index = {
        (_normalize(m["customerName"]), (m.get("ratePlanCode") or "").upper()): m.get("skuKey") or ""
        for m in cust_maps
    }
    return mapping_index, cust_map_index


def _resolve_sku(cust_norm: str, promo_code: str,
                 mapping_index: dict, cust_map_index: dict,
                 billing_plan: str = "") -> str:
    # Customer-specific promo map
    if promo_code:
        ck = (cust_norm, promo_code)
        if ck in cust_map_index:
            return cust_map_index[ck]
        if promo_code in mapping_index:
            return mapping_index[promo_code]
    # Billing plan fallback
    if billing_plan:
        bp = billing_plan.upper()
        ck2 = (cust_norm, bp)
        if ck2 in cust_map_index:
            return cust_map_index[ck2]
        if bp in mapping_index:
            return mapping_index[bp]
    return "UNMAPPED"


# ---------------------------------------------------------------------------
# GET /api/reports/summary
# ---------------------------------------------------------------------------

@router.get("/reports/summary")
async def get_reports_summary():
    """
    Returns all data needed for the 7 Reports tabs in a single call.
    """
    from .customers import (
        _sync_cache,
        billing_frequency_overrides,
        enrich_customer,
    )
    from .reconciliation import get_reconciliation

    contracts = _sync_cache.get("contracts") or []
    if not contracts:
        raise HTTPException(
            status_code=503,
            detail="No MyAdmin data cached. Open the Customers page to trigger a sync first."
        )

    # ── Shared indices ──────────────────────────────────────────────────────
    ovr_index, catalog_index = _build_price_index()
    mapping_index, cust_map_index = _build_sku_index()

    # ── Billing-type lookup from enriched customers ─────────────────────────
    raw_customers = _sync_cache.get("raw_customers") or []
    bt_by_cid: Dict[str, str] = {}
    bf_by_norm: Dict[str, str] = {}   # normalised name -> billingFrequency
    for rc in raw_customers:
        ec = enrich_customer(rc)
        cid = str(rc.get("companyId") or "")
        if cid:
            bt_by_cid[cid] = ec.get("billingType") or "Standard"
        freq = ec.get("billingFrequency") or ""
        if freq:
            bf_by_norm[_normalize(ec.get("customerName") or "")] = freq

    # ── Partition: active vs terminated ────────────────────────────────────
    active_contracts     = [c for c in contracts if not c.get("isTerminated")]
    terminated_contracts = [c for c in contracts if c.get("isTerminated")]

    # ═══════════════════════════════════════════════════════════════════════
    # 1. MRR BY BILLING TYPE
    # ═══════════════════════════════════════════════════════════════════════
    mrr_by_type: Dict[str, float] = defaultdict(float)
    device_count_by_type: Dict[str, int] = defaultdict(int)

    for c in active_contracts:
        cid      = _company_id(c)
        cname    = _customer_name(c)
        bt       = bt_by_cid.get(cid) or "Standard"
        cust_norm = _normalize(cname)
        promo    = (c.get("promoCode") or "").upper()
        adp_name = (c.get("activeDevicePlan") or {}).get("name") or ""
        sku_key  = _resolve_sku(cust_norm, promo, mapping_index, cust_map_index, adp_name)
        if sku_key == "UNMAPPED":
            sku_key = adp_name or "UNMAPPED"
        rate = _resolve_monthly_rate(cname, sku_key, ovr_index, catalog_index)
        mrr_by_type[bt] += rate
        device_count_by_type[bt] += 1

    mrr_by_type     = {k: round(v, 2) for k, v in mrr_by_type.items()}
    total_mrr       = round(sum(mrr_by_type.values()), 2)
    total_active    = sum(device_count_by_type.values())

    # ═══════════════════════════════════════════════════════════════════════
    # 2. PORTFOLIO HEALTH  (per-customer status counts)
    # ═══════════════════════════════════════════════════════════════════════
    # Reuse reconciliation endpoint data
    try:
        recon_data = await get_reconciliation()
        recon_summary  = recon_data.get("summary") or {}
        recon_customers = recon_data.get("customers") or []
    except Exception:
        recon_summary   = {}
        recon_customers = []

    status_counts = {"ok": 0, "discrepancy": 0, "unmapped": 0, "no_price": 0, "not_in_qb": 0}
    for rc in recon_customers:
        s = rc.get("status") or "ok"
        status_counts[s] = status_counts.get(s, 0) + 1

    # ═══════════════════════════════════════════════════════════════════════
    # 3. PRICE DISCREPANCY LEADERBOARD
    # ═══════════════════════════════════════════════════════════════════════
    discrepancies = [
        {
            "customerId":      rc["customerId"],
            "customerName":    rc["customerName"],
            "expectedMonthly": rc.get("expectedMonthly") or 0,
            "actualMonthly":   rc.get("actualMonthly") or 0,
            "delta":           rc.get("delta") or 0,
            "deviceCount":     rc.get("deviceCount") or 0,
            "status":          rc.get("status") or "ok",
        }
        for rc in recon_customers
        if abs(rc.get("delta") or 0) > 0.01
    ]
    discrepancies.sort(key=lambda x: abs(x["delta"]), reverse=True)

    # ═══════════════════════════════════════════════════════════════════════
    # 4. UNMAPPED DEVICES
    # ═══════════════════════════════════════════════════════════════════════
    unmapped_devices = []
    for rc in recon_customers:
        for dev in rc.get("devices") or []:
            if dev.get("skuKey") == "UNMAPPED" or dev.get("status") == "unmapped":
                unmapped_devices.append({
                    "customerName": rc["customerName"],
                    "serialNumber": dev.get("serialNumber") or "",
                    "ratePlanCode": dev.get("ratePlanCode") or "",
                    "promoCode":    dev.get("promoCode") or "",
                    "skuKey":       dev.get("skuKey") or "UNMAPPED",
                    "status":       dev.get("status") or "unmapped",
                })
    unmapped_devices.sort(key=lambda x: x["customerName"])

    # ═══════════════════════════════════════════════════════════════════════
    # 5. ACTIVATIONS TREND  (last 6 complete months using contracts cache)
    # ═══════════════════════════════════════════════════════════════════════
    today     = date.today()
    # Build list of the last 6 complete calendar months
    months_6  = []
    yr, mo = today.year, today.month
    mo -= 1
    if mo == 0:
        mo, yr = 12, yr - 1
    for _ in range(6):
        months_6.append(f"{yr}-{mo:02d}")
        mo -= 1
        if mo == 0:
            mo, yr = 12, yr - 1
    months_6.reverse()   # oldest first

    activations_by_month: Dict[str, int] = {m: 0 for m in months_6}
    for c in active_contracts:
        fcd = _safe_date(c.get("firstDeviceActivationDate"))
        if not fcd:
            fcd = _safe_date(c.get("billingStartDate"))
        if not fcd:
            continue
        mk = _month_key(fcd)
        if mk in activations_by_month:
            activations_by_month[mk] += 1

    activations_trend = [
        {"month": m, "label": _month_label(m), "count": activations_by_month[m]}
        for m in months_6
    ]

    # ═══════════════════════════════════════════════════════════════════════
    # 6. ANNUAL BILLING CUSTOMERS
    # ═══════════════════════════════════════════════════════════════════════
    annual_customers = []
    seen_annual: set = set()
    for rc in raw_customers:
        ec = enrich_customer(rc)
        if (ec.get("billingFrequency") or "").lower() == "annual":
            cname = ec.get("customerName") or ""
            norm  = _normalize(cname)
            if norm in seen_annual:
                continue
            seen_annual.add(norm)
            # Count active devices for this customer
            cid = str(rc.get("companyId") or "")
            dev_count = sum(
                1 for c in active_contracts
                if _company_id(c) == cid
            )
            bsm = ec.get("billingStartMonth") or ""
            annual_customers.append({
                "customerName":    cname,
                "customerId":      cid,
                "billingStartMonth": bsm,
                "billingStartLabel": _month_label(bsm) if bsm else "—",
                "deviceCount":     dev_count,
                "estimatedAnnual": round(
                    sum(
                        _resolve_monthly_rate(
                            cname,
                            _resolve_sku(
                                _normalize(cname),
                                (c.get("promoCode") or "").upper(),
                                mapping_index, cust_map_index,
                                (c.get("activeDevicePlan") or {}).get("name") or ""
                            ),
                            ovr_index, catalog_index
                        ) * 12
                        for c in active_contracts
                        if _company_id(c) == cid
                    ), 2
                ),
            })
    annual_customers.sort(key=lambda x: x["estimatedAnnual"], reverse=True)

    # ═══════════════════════════════════════════════════════════════════════
    # 7. TERMINATED DEVICES — month-over-month (last 12 months)
    # ═══════════════════════════════════════════════════════════════════════
    months_12 = []
    yr2, mo2 = today.year, today.month
    for _ in range(13):   # include current partial month
        months_12.append(f"{yr2}-{mo2:02d}")
        mo2 -= 1
        if mo2 == 0:
            mo2, yr2 = 12, yr2 - 1
    months_12.reverse()

    term_by_month: Dict[str, int] = {m: 0 for m in months_12}
    term_detail: List[dict] = []

    for c in terminated_contracts:
        end_raw = c.get("endDate") or c.get("billingStartDate") or ""
        end_d   = _safe_date(end_raw)
        if not end_d:
            continue
        mk = _month_key(end_d)
        if mk in term_by_month:
            term_by_month[mk] += 1
        cname = _customer_name(c)
        cid   = _company_id(c)
        bt    = bt_by_cid.get(cid) or "Standard"
        promo = (c.get("promoCode") or "").upper()
        adp   = (c.get("activeDevicePlan") or {}).get("name") or ""
        sku   = _resolve_sku(_normalize(cname), promo, mapping_index, cust_map_index, adp)
        rate  = _resolve_monthly_rate(cname, sku, ovr_index, catalog_index)
        term_detail.append({
            "serialNumber": _serial(c),
            "customerName": cname,
            "billingType":  bt,
            "endDate":      str(end_d),
            "endMonth":     mk,
            "skuKey":       sku,
            "monthlyRate":  round(rate, 2),
        })

    term_detail.sort(key=lambda x: x["endDate"], reverse=True)

    terminated_trend = [
        {
            "month":  m,
            "label":  _month_label(m),
            "count":  term_by_month[m],
            "isCurrentMonth": m == _month_key(today),
        }
        for m in months_12
    ]

    # ── Aggregate terminated by customer for leaderboard ────────────────────
    term_by_customer: Dict[str, dict] = defaultdict(lambda: {"count": 0, "mrr_lost": 0.0})
    for td in term_detail:
        mk = td["endMonth"]
        if mk in term_by_month:   # only last 12 months
            k = td["customerName"]
            term_by_customer[k]["count"]    += 1
            term_by_customer[k]["mrr_lost"] += td["monthlyRate"]
    term_customer_list = [
        {
            "customerName": k,
            "count":        v["count"],
            "mrrLost":      round(v["mrr_lost"], 2),
        }
        for k, v in term_by_customer.items()
    ]
    term_customer_list.sort(key=lambda x: x["count"], reverse=True)

    # ── Summary counts ───────────────────────────────────────────────────────
    total_terminated = len(terminated_contracts)
    term_this_month  = term_by_month.get(_month_key(today), 0)
    term_last_month  = term_by_month.get(months_12[-3] if len(months_12) >= 3 else "", 0)

    return {
        # ── meta ──────────────────────────────────────────────────────────
        "generatedAt":   datetime.utcnow().isoformat() + "Z",
        "totalActive":   total_active,
        "totalTerminated": total_terminated,

        # ── tab 1: MRR by billing type ────────────────────────────────────
        "mrr": {
            "totalMRR":         total_mrr,
            "byBillingType":    mrr_by_type,
            "devicesByType":    dict(device_count_by_type),
        },

        # ── tab 2: portfolio health ───────────────────────────────────────
        "portfolioHealth": {
            "summary":   recon_summary,
            "statusCounts": status_counts,
        },

        # ── tab 3: discrepancy leaderboard ────────────────────────────────
        "discrepancies": discrepancies[:50],

        # ── tab 4: unmapped devices ───────────────────────────────────────
        "unmapped": unmapped_devices,

        # ── tab 5: activations trend ──────────────────────────────────────
        "activationsTrend": activations_trend,

        # ── tab 6: annual billing ─────────────────────────────────────────
        "annualCustomers": annual_customers,

        # ── tab 7: terminated trend ───────────────────────────────────────
        "terminatedTrend": {
            "byMonth":       terminated_trend,
            "recentDevices": term_detail[:100],
            "byCustomer":    term_customer_list[:30],
            "thisMonth":     term_this_month,
            "lastMonth":     term_last_month,
            "totalTracked":  total_terminated,
        },
    }
