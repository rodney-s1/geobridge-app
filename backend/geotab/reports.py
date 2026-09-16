"""
GeoBridge Reports API
=====================
Read-only analytics derived from the MyAdmin contracts cache and
reconciliation indices.  Nothing here syncs to QuickBooks.

Endpoints
---------
GET /api/reports/summary          — full data bundle for all 7 report tabs
GET /api/reports/terminated        — terminated devices with month-over-month trend
GET /api/reports/profit           — per-customer profit from actual QB invoice lines
GET /api/reports/profit/export    — same data as an .xlsx workbook download
"""
from __future__ import annotations

import html as _html
import io
import os
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .auth import require_session

router = APIRouter(dependencies=[Depends(require_session)])

from ._data_dir import _DATA_DIR, _HERE


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
    catalog   = _load(os.path.join(_DATA_DIR, "sku_catalog.json"), [])
    overrides = _load(os.path.join(_DATA_DIR, "sku_customer_overrides.json"), [])
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


def _build_cost_override_index() -> Dict[Tuple[str, str], float]:
    """
    (normalizedCustomerName, skuKey) -> cost, for the subset of per-customer
    price overrides that also have a cost override set (costSet=True --
    see settings.py's upsert_override). Lets a specific customer get a
    lower negotiated cost for a SKU while everyone else stays on the SKU's
    catalog cost.
    """
    overrides = _load(os.path.join(_DATA_DIR, "sku_customer_overrides.json"), [])
    return {
        (_normalize(o["customerName"]), o["skuKey"]): float(o.get("cost") or 0.0)
        for o in overrides
        if o.get("costSet")
    }


def _resolve_cost(customer_name: str, sku_key: str,
                   cost_ovr_index: Dict[Tuple[str, str], float],
                   cost_index: Dict[str, Optional[float]]) -> Optional[float]:
    """
    Resolve the effective cost for a (customer, SKU) invoice line:
      1. A per-customer cost override for this exact SKU, if one exists
         (or for the parent of a dash-department sub-account -- same
         precedence rule as _resolve_monthly_rate).
      2. Otherwise, the SKU's catalog cost (None if never confirmed there
         either -- the "Cost Data Incomplete" case).
    """
    norm = _normalize(customer_name)
    dash = norm.find(" - ")
    if dash != -1:
        parent_key = (norm[:dash].strip(), sku_key)
        if parent_key in cost_ovr_index:
            return cost_ovr_index[parent_key]
    key = (norm, sku_key)
    if key in cost_ovr_index:
        return cost_ovr_index[key]
    return cost_index.get(sku_key)


# ---------------------------------------------------------------------------
# SKU resolution (simplified — promo-code → sku_key → monthly rate)
# ---------------------------------------------------------------------------

def _build_sku_index() -> Tuple[dict, dict]:
    """Returns (mapping_index, cust_map_index)."""
    mappings  = _load(os.path.join(_DATA_DIR, "sku_mappings.json"), [])
    cust_maps = _load(os.path.join(_DATA_DIR, "customer_rate_plan_mappings.json"), [])
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


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/reports/profit
# ═══════════════════════════════════════════════════════════════════════════

def _build_cost_index() -> Dict[str, Optional[float]]:
    """
    skuKey -> cost, or None if this SKU has never had cost data confirmed
    for it. None (not 0.0) is used as the "missing" sentinel so a genuinely
    free/zero-cost SKU isn't confused with one that simply hasn't been
    priced yet.

    A cost counts as "confirmed" (returned, even if 0.0) when either:
      - costSet is True -- the user explicitly saved a cost (including
        $0.00) via Settings -> SKU Catalog's manual Add/Edit form, or
      - cost is a real nonzero number -- imported from a QB Item Price
        List row that had an actual cost value.
    A bare falsy `cost` with no costSet flag (the common case for catalog
    rows that have simply never had cost data touched at all) is "missing".
    """
    catalog = _load(os.path.join(_DATA_DIR, "sku_catalog.json"), [])
    index: Dict[str, Optional[float]] = {}
    for s in catalog:
        cost = s.get("cost")
        if s.get("costSet") or cost:
            index[s["skuKey"]] = float(cost or 0.0)
        else:
            index[s["skuKey"]] = None
    return index


def _build_profit_workbook(report: dict) -> bytes:
    """
    Render the profit report dict (see _compute_profit_report) as a styled
    .xlsx workbook with three sheets: Summary, Profit by Customer, and
    Line Detail. Returns the raw workbook bytes (write to a BytesIO/response,
    never to disk -- Cloudflare-style stateless generation).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import CellIsRule

    # ── Shared style palette ────────────────────────────────────────────────
    NAVY       = "1F2937"   # header band
    NAVY_DARK  = "111827"   # title band
    AMBER      = "F59E0B"
    AMBER_FILL = "FEF3C7"
    GREEN      = "059669"
    RED        = "DC2626"
    WHITE_FONT = Font(color="FFFFFF", bold=True, size=11)
    TITLE_FONT = Font(color="FFFFFF", bold=True, size=16)
    SUBTLE     = Font(color="6B7280", italic=True, size=9)
    BOLD       = Font(bold=True)
    HEADER_FILL = PatternFill("solid", fgColor=NAVY)
    TITLE_FILL  = PatternFill("solid", fgColor=NAVY_DARK)
    STRIPE_FILL = PatternFill("solid", fgColor="F3F4F6")
    AMBER_ROW_FILL = PatternFill("solid", fgColor=AMBER_FILL)
    THIN = Side(style="thin", color="D1D5DB")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    MONEY_FMT = '$#,##0.00'
    PCT_FMT   = '0.0"%"'

    wb = Workbook()

    def _style_header_row(ws, row_idx, ncols):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=row_idx, column=c)
            cell.font = WHITE_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = BORDER
        ws.row_dimensions[row_idx].height = 28

    def _title_band(ws, text, ncols, subtitle=None):
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        cell = ws.cell(row=1, column=1, value=text)
        cell.font = TITLE_FONT
        cell.fill = TITLE_FILL
        cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 32
        for c in range(1, ncols + 1):
            ws.cell(row=1, column=c).fill = TITLE_FILL
        next_row = 2
        if subtitle:
            ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
            sub_cell = ws.cell(row=2, column=1, value=subtitle)
            sub_cell.font = SUBTLE
            sub_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            ws.row_dimensions[2].height = 18
            next_row = 3
        return next_row

    sum_data      = report.get("summary", {})
    customer_list = report.get("customers", [])
    missing_list  = report.get("skusMissingCost", [])

    # ═══════════════════════════════════════════════════════════════════════
    # Sheet 1 — Summary
    # ═══════════════════════════════════════════════════════════════════════
    ws1 = wb.active
    ws1.title = "Summary"
    ws1.sheet_view.showGridLines = False
    generated = report.get("generatedAt", "")
    _title_band(ws1, "GeoBridge — Profit Report", 4,
                subtitle=f"Generated {generated}  •  Revenue and cost from the last imported QB invoice × Item Price List cost data")

    # Stat cards, rendered as a simple 2-column label/value block
    stats = [
        ("Total Revenue",  sum_data.get("totalRevenue", 0),  MONEY_FMT, None),
        ("Total Cost",     sum_data.get("totalCost", 0),     MONEY_FMT, None),
        ("Total Profit",   sum_data.get("totalProfit", 0),   MONEY_FMT, None),
        ("Margin %",       sum_data.get("marginPct", 0),     PCT_FMT,   None),
        ("Customers",      sum_data.get("customerCount", 0), '#,##0',   None),
        ("Customers with Incomplete Cost Data", sum_data.get("customersWithIncompleteCost", 0), '#,##0', None),
        ("SKUs Missing Cost Data", sum_data.get("skusMissingCostCount", 0), '#,##0', None),
    ]
    r = 4
    for label, value, fmt, _ in stats:
        lbl_cell = ws1.cell(row=r, column=1, value=label)
        lbl_cell.font = BOLD
        val_cell = ws1.cell(row=r, column=2, value=value)
        val_cell.number_format = fmt
        val_cell.font = Font(bold=True, size=12,
                              color=GREEN if label == "Total Profit" and value >= 0 else
                                    (RED if label == "Total Profit" else "111827"))
        r += 1

    r += 1
    if missing_list:
        note = ws1.cell(row=r, column=1,
                         value="⚠ Cost Data Incomplete — SKUs below have never had a cost confirmed. "
                               "Their lines are treated as $0 cost above, so profit may be overstated.")
        note.font = Font(color=AMBER, bold=True, italic=True)
        ws1.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        r += 2

        hdr_row = r
        headers = ["SKU", "Category", "Customers Affected", "Total Qty Billed"]
        for c, h in enumerate(headers, start=1):
            ws1.cell(row=hdr_row, column=c, value=h)
        _style_header_row(ws1, hdr_row, len(headers))
        r += 1
        for i, s in enumerate(missing_list):
            row_fill = AMBER_ROW_FILL if i % 2 == 0 else None
            vals = [s.get("skuKey", ""), s.get("category", "") or "—",
                    s.get("customerCount", 0), s.get("qtyTotal", 0)]
            for c, v in enumerate(vals, start=1):
                cell = ws1.cell(row=r, column=c, value=v)
                cell.border = BORDER
                if row_fill:
                    cell.fill = row_fill
                if c >= 3:
                    cell.alignment = Alignment(horizontal="right")
            r += 1

    ws1.column_dimensions["A"].width = 42
    ws1.column_dimensions["B"].width = 22
    ws1.column_dimensions["C"].width = 20
    ws1.column_dimensions["D"].width = 18
    ws1.freeze_panes = "A4"

    # ═══════════════════════════════════════════════════════════════════════
    # Sheet 2 — Profit by Customer
    # ═══════════════════════════════════════════════════════════════════════
    ws2 = wb.create_sheet("Profit by Customer")
    ws2.sheet_view.showGridLines = False
    headers2 = ["Customer", "Qty Billed", "Revenue", "Cost", "Profit", "Margin %", "Cost Data"]
    ncols2 = len(headers2)
    start_row = _title_band(ws2, "Profit by Customer", ncols2,
                             subtitle="Sorted lowest profit first — prioritize reviewing these accounts")
    hdr_row2 = start_row
    for c, h in enumerate(headers2, start=1):
        ws2.cell(row=hdr_row2, column=c, value=h)
    _style_header_row(ws2, hdr_row2, ncols2)

    r = hdr_row2 + 1
    for i, cust in enumerate(customer_list):
        row_fill = STRIPE_FILL if i % 2 == 0 else None
        vals = [
            cust.get("customerName", ""),
            cust.get("deviceQty", 0),
            cust.get("revenue", 0.0),
            cust.get("cost", 0.0),
            cust.get("profit", 0.0),
            cust.get("marginPct", 0.0),
            "Incomplete" if cust.get("costIncomplete") else "Complete",
        ]
        for c, v in enumerate(vals, start=1):
            cell = ws2.cell(row=r, column=c, value=v)
            cell.border = BORDER
            if row_fill:
                cell.fill = row_fill
            if c in (3, 4, 5):
                cell.number_format = MONEY_FMT
                cell.alignment = Alignment(horizontal="right")
            elif c == 6:
                cell.number_format = PCT_FMT
                cell.alignment = Alignment(horizontal="right")
            elif c == 2:
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal="right")
            if c == 5:  # Profit column
                cell.font = Font(bold=True, color=GREEN if v >= 0 else RED)
            if c == 7 and v == "Incomplete":
                cell.font = Font(color=AMBER, bold=True)
                cell.fill = AMBER_ROW_FILL if not row_fill else PatternFill(
                    "solid", fgColor=AMBER_FILL)
        r += 1

    last_data_row2 = r - 1
    if last_data_row2 >= hdr_row2 + 1:
        # Conditional formatting on Profit column: red text for negatives.
        profit_col = get_column_letter(5)
        rng = f"{profit_col}{hdr_row2+1}:{profit_col}{last_data_row2}"
        ws2.conditional_formatting.add(
            rng,
            CellIsRule(operator="lessThan", formula=["0"],
                       font=Font(color=RED, bold=True))
        )

    widths2 = [34, 12, 16, 16, 16, 12, 14]
    for i, w in enumerate(widths2, start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.freeze_panes = f"A{hdr_row2+1}"
    ws2.auto_filter.ref = f"A{hdr_row2}:{get_column_letter(ncols2)}{max(last_data_row2, hdr_row2)}"

    # ═══════════════════════════════════════════════════════════════════════
    # Sheet 3 — Line Detail (one row per customer x SKU invoice line)
    # ═══════════════════════════════════════════════════════════════════════
    ws3 = wb.create_sheet("Line Detail")
    ws3.sheet_view.showGridLines = False
    headers3 = ["Customer", "SKU", "Qty", "Unit Price", "Unit Cost", "Revenue", "Profit", "Cost Data"]
    ncols3 = len(headers3)
    start_row3 = _title_band(ws3, "Line Detail", ncols3,
                              subtitle="One row per customer × SKU invoice line from the last QB import")
    hdr_row3 = start_row3
    for c, h in enumerate(headers3, start=1):
        ws3.cell(row=hdr_row3, column=c, value=h)
    _style_header_row(ws3, hdr_row3, ncols3)

    r = hdr_row3 + 1
    stripe_i = 0
    for cust in customer_list:
        for line in cust.get("lines", []):
            row_fill = STRIPE_FILL if stripe_i % 2 == 0 else None
            stripe_i += 1
            cost_incomplete = line.get("costIncomplete")
            vals = [
                cust.get("customerName", ""),
                line.get("skuKey", ""),
                line.get("qty", 0),
                line.get("price", 0.0),
                line.get("cost") if line.get("cost") is not None else 0.0,
                line.get("revenue", 0.0),
                line.get("profit", 0.0),
                "Incomplete" if cost_incomplete else "Complete",
            ]
            for c, v in enumerate(vals, start=1):
                cell = ws3.cell(row=r, column=c, value=v)
                cell.border = BORDER
                if row_fill:
                    cell.fill = row_fill
                if c in (4, 5, 6, 7):
                    cell.number_format = MONEY_FMT
                    cell.alignment = Alignment(horizontal="right")
                elif c == 3:
                    cell.number_format = '#,##0'
                    cell.alignment = Alignment(horizontal="right")
                if c == 7:
                    cell.font = Font(color=GREEN if v >= 0 else RED)
                if c == 8 and v == "Incomplete":
                    cell.font = Font(color=AMBER, bold=True)
            r += 1

    last_data_row3 = r - 1
    widths3 = [30, 32, 8, 12, 12, 14, 14, 13]
    for i, w in enumerate(widths3, start=1):
        ws3.column_dimensions[get_column_letter(i)].width = w
    ws3.freeze_panes = f"A{hdr_row3+1}"
    ws3.auto_filter.ref = f"A{hdr_row3}:{get_column_letter(ncols3)}{max(last_data_row3, hdr_row3)}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _compute_profit_report() -> dict:
    """
    Per-customer profit computed from ACTUAL QuickBooks invoice quantities
    (qb_invoice_quantities.json) — i.e. what was really billed last import,
    not the MyAdmin device count or Reconciliation's expected total. This is
    intentionally independent of Reconciliation status: a customer with a
    device-count mismatch still gets a profit number based on what QB says
    they were actually charged.

    For each (customer, SKU) invoice line:
      revenue = qbQty * price   (customer override price, else catalog defaultPrice)
      cost    = qbQty * cost    (from the Item Price List import)
      profit  = revenue - cost

    If a SKU has never had cost data imported (Item Price List), its line's
    cost is treated as $0 for the total (so profit is NOT understated), but
    the line -- and the customer, and the SKU -- are flagged as having
    incomplete cost data so the user knows which SKUs still need a cost
    entered via Settings -> Import Price List (or manually).

    QB-authoritative SKUs (e.g. BlueArrow Fuel Service, billed from an
    external platform with no MyAdmin device count) ARE included here --
    unlike Reconciliation, which excludes them from its device-count delta,
    profit is a pure revenue-vs-cost calculation per invoice line and every
    billed SKU should count toward it.

    Shared by both GET /api/reports/profit (JSON, for the UI) and
    GET /api/reports/profit/export (Excel workbook) so the two never drift.
    """
    catalog   = _load(os.path.join(_DATA_DIR, "sku_catalog.json"), [])
    qb_qtys   = _load(os.path.join(_DATA_DIR, "qb_invoice_quantities.json"), [])

    if not qb_qtys:
        raise HTTPException(
            status_code=503,
            detail="No QB invoice data found. Import a QB CSV from Settings first."
        )

    ovr_index, catalog_index = _build_price_index()
    cost_index      = _build_cost_index()
    cost_ovr_index  = _build_cost_override_index()
    catalog_cat = {s["skuKey"]: s.get("category") or "" for s in catalog}

    # Aggregate incomplete-cost SKUs across the whole report (for the top-level
    # "SKUs missing cost data" list the user asked for).
    skus_missing_cost: Dict[str, dict] = {}   # skuKey -> {skuKey, category, customerCount, qtyTotal}

    customers: Dict[str, dict] = {}
    for row in qb_qtys:
        cname   = row.get("customerName") or ""
        sku_key = row.get("skuKey") or ""
        qty     = int(row.get("qbQty") or 0)
        if not cname or not sku_key or qty <= 0:
            continue

        price = _resolve_monthly_rate(cname, sku_key, ovr_index, catalog_index)
        cost  = _resolve_cost(cname, sku_key, cost_ovr_index, cost_index)  # None => missing cost data
        cost_missing = cost is None
        cost_val = cost or 0.0

        revenue = round(qty * price, 2)
        cost_total = round(qty * cost_val, 2)
        profit = round(revenue - cost_total, 2)

        cust = customers.setdefault(cname, {
            "customerName":     cname,
            "revenue":          0.0,
            "cost":             0.0,
            "profit":           0.0,
            "deviceQty":        0,
            "costIncomplete":   False,
            "incompleteSkus":   [],
            "lines":            [],
        })
        cust["revenue"]  += revenue
        cust["cost"]     += cost_total
        cust["profit"]   += profit
        cust["deviceQty"] += qty
        if cost_missing:
            cust["costIncomplete"] = True
            if sku_key not in cust["incompleteSkus"]:
                cust["incompleteSkus"].append(sku_key)

        cust["lines"].append({
            "skuKey":         sku_key,
            "qty":            qty,
            "price":          round(price, 2),
            "cost":           round(cost_val, 2) if not cost_missing else None,
            "revenue":        revenue,
            "profit":         profit,
            "costIncomplete": cost_missing,
        })

        if cost_missing:
            entry = skus_missing_cost.setdefault(sku_key, {
                "skuKey":       sku_key,
                "category":     catalog_cat.get(sku_key, ""),
                "customerCount": 0,
                "qtyTotal":     0,
            })
            entry["customerCount"] += 1
            entry["qtyTotal"]      += qty

    customer_list = []
    for c in customers.values():
        c["revenue"] = round(c["revenue"], 2)
        c["cost"]    = round(c["cost"], 2)
        c["profit"]  = round(c["profit"], 2)
        c["marginPct"] = round((c["profit"] / c["revenue"]) * 100, 1) if c["revenue"] else 0.0
        c["lines"].sort(key=lambda l: l["revenue"], reverse=True)
        c["incompleteSkus"].sort()
        customer_list.append(c)

    customer_list.sort(key=lambda x: x["profit"])   # lowest profit / worst margin first

    total_revenue = round(sum(c["revenue"] for c in customer_list), 2)
    total_cost    = round(sum(c["cost"] for c in customer_list), 2)
    total_profit  = round(total_revenue - total_cost, 2)
    total_margin  = round((total_profit / total_revenue) * 100, 1) if total_revenue else 0.0
    customers_with_incomplete = sum(1 for c in customer_list if c["costIncomplete"])

    missing_list = sorted(
        skus_missing_cost.values(),
        key=lambda x: x["qtyTotal"], reverse=True
    )

    return {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "summary": {
            "totalRevenue":  total_revenue,
            "totalCost":     total_cost,
            "totalProfit":   total_profit,
            "marginPct":     total_margin,
            "customerCount": len(customer_list),
            "customersWithIncompleteCost": customers_with_incomplete,
            "skusMissingCostCount": len(missing_list),
        },
        "customers": customer_list,
        "skusMissingCost": missing_list,
    }


@router.get("/reports/profit")
async def get_profit_report():
    if not _load(os.path.join(_DATA_DIR, "qb_invoice_quantities.json"), []):
        raise HTTPException(
            status_code=503,
            detail="No QB invoice data found. Import a QB CSV from Settings first."
        )
    return _compute_profit_report()


@router.get("/reports/profit/export")
async def export_profit_report():
    """
    Same data as GET /api/reports/profit, rendered as a styled .xlsx workbook:
      - "Summary"           overview cards + the "Cost Data Incomplete" SKU list
      - "Profit by Customer" one row per customer, sorted worst-profit-first
      - "Line Detail"        one row per (customer, SKU) invoice line
    """
    if not _load(os.path.join(_DATA_DIR, "qb_invoice_quantities.json"), []):
        raise HTTPException(
            status_code=503,
            detail="No QB invoice data found. Import a QB CSV from Settings first."
        )
    report = _compute_profit_report()
    workbook_bytes = _build_profit_workbook(report)

    filename = f"GeoBridge_Profit_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(workbook_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
