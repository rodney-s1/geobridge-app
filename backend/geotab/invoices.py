"""
invoices.py — Prorated invoice generation engine
=================================================

Generates prorated invoice line items for Charge Upon Activation (CUA) and
Hanover customers whose devices had a firstConnectDate (First Connect Date)
within the requested billing month.

QB invoice line item structure (mirrors the BlueArrow Invoice Template):
  - ITEM CODE  : skuKey (e.g. "Geotab Service:Geotab Service (GO + Support)")
  - DESCRIPTION: "Service Fee <SKU name> - New Activations\n
                  Prorated <Month Day> through <Month LastDay Year> for devices:\n
                  <serial1>\n<serial2>..."
  - QUANTITY   : number of devices in this group
  - PRICE EACH : prorated daily rate × days_active  (= monthly_rate × prorate_factor)
  - CLASS      : customer's QB class (from customer record, if available)
  - AMOUNT     : QUANTITY × PRICE EACH
  - TAX        : Tax

Devices are grouped by (skuKey, firstConnectDate) so that devices activating
on the same day with the same SKU share one line item — exactly matching the
QB memorized transaction format shown in the screenshot.

A final "Full Month Service" line item is also produced for the NEXT month,
listing all newly activated devices at the full monthly rate (qty = total
newly activated devices). This mirrors the "July Service" line at $18.80 × 23.
"""

from __future__ import annotations

import calendar
import os
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

# Re-use the same normaliser and price-resolver already used in reconciliation
from .reconciliation import _normalize, _resolve_price

# Shared in-memory cache populated by customers.py sync
from .customers import _sync_cache

# --------------------------------------------------------------------------- #
#  File paths (same dir as all other geotab data files)                        #
# --------------------------------------------------------------------------- #
_HERE = os.path.dirname(os.path.abspath(__file__))

router = APIRouter()

# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _load_json(path: str, default):
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _prorate_factor(first_connect: date, billing_year: int, billing_month: int) -> Tuple[int, int, float]:
    """
    Returns (days_active, days_in_month, factor).
    days_active is inclusive: first_connect_day → last day of month.
    """
    dim = _days_in_month(billing_year, billing_month)
    last_day = date(billing_year, billing_month, dim)
    days_active = (last_day - first_connect).days + 1
    days_active = max(1, min(days_active, dim))   # clamp 1..dim
    factor = round(days_active / dim, 6)
    return days_active, dim, factor


def _month_label(year: int, month: int) -> str:
    """Returns e.g. 'June 2026'"""
    return date(year, month, 1).strftime("%B %Y")


def _month_last_day_label(year: int, month: int) -> str:
    """Returns e.g. 'June 30 2026'"""
    dim = _days_in_month(year, month)
    d = date(year, month, dim)
    return f"{d.strftime('%B')} {d.day} {year}"


def _connect_day_label(d: date) -> str:
    """Returns e.g. 'June 18'"""
    return f"{d.strftime('%B')} {d.day}"


# --------------------------------------------------------------------------- #
#  SKU resolution helpers (mirrors reconciliation 4-tier logic)                #
# --------------------------------------------------------------------------- #

def _build_indices():
    """
    Build the same lookup indices reconciliation.py uses:
      catalog_index  : skuKey -> defaultPrice
      ovr_index      : (norm_customerName, skuKey) -> override price
      mapping_index  : ratePlanCode_upper -> skuKey   (global)
      cust_map_index : (norm_customerName, ratePlanCode_upper) -> skuKey
      full_path_index: skuKey -> fullPath  (QB item code format)
      sku_desc_index : skuKey -> desc  (human label for description)
    """
    catalog  = _load_json(os.path.join(_HERE, "sku_catalog.json"), [])
    mappings = _load_json(os.path.join(_HERE, "sku_mappings.json"), [])
    cust_maps= _load_json(os.path.join(_HERE, "customer_rate_plan_mappings.json"), [])
    overrides= _load_json(os.path.join(_HERE, "sku_customer_overrides.json"), [])

    catalog_index: Dict[str, float] = {
        s["skuKey"]: float(s.get("defaultPrice") or 0)
        for s in catalog
    }
    full_path_index: Dict[str, str] = {
        s["skuKey"]: s.get("fullPath") or s["skuKey"]
        for s in catalog
    }
    sku_desc_index: Dict[str, str] = {
        s["skuKey"]: s.get("desc") or s["skuKey"]
        for s in catalog
    }
    ovr_index: Dict[tuple, float] = {
        (_normalize(o["customerName"]), o["skuKey"]): float(o.get("price") or 0)
        for o in overrides
    }
    mapping_index: Dict[str, str] = {
        (m.get("ratePlanCode") or "").upper(): m.get("skuKey") or ""
        for m in mappings
    }
    cust_map_index: Dict[tuple, str] = {
        (_normalize(m["customerName"]), (m.get("ratePlanCode") or "").upper()): m.get("skuKey") or ""
        for m in cust_maps
    }

    return catalog_index, ovr_index, mapping_index, cust_map_index, full_path_index, sku_desc_index


def _resolve_sku(customer_norm: str, rate_plan_code: str,
                 mapping_index: dict, cust_map_index: dict) -> Optional[str]:
    """
    4-tier SKU resolution (same tiers as reconciliation.py):
      Tier 1: customer-specific mapping on ratePlanCode
      Tier 2: global mapping on ratePlanCode
      Returns None if no mapping found.
    """
    code = (rate_plan_code or "").upper()
    return (
        cust_map_index.get((customer_norm, code))
        or mapping_index.get(code)
        or None
    )


# --------------------------------------------------------------------------- #
#  Hanover SKU detection                                                       #
# --------------------------------------------------------------------------- #

# SKU keys that are billed to Hanover Insurance Group rather than the customer.
# Cameras / Aux bundles are NEVER included here — those stay with the customer.
HANOVER_SKU_KEYS: set = {
    "Geotab Service Fee (HANOVER)",
    "Service Fee (HANOVER-CS) Han",
}

# Han-CS dual-SKU constants
# Each Han-CS device generates TWO line items: one at $8 for the sub-customer
# and one at $8 for the Hanover master invoice.
HAN_CS_CUST_SKU = "Service Fee (HANOVER-CS) Cust"   # billed to sub-customer
HAN_CS_HAN_SKU  = "Service Fee (HANOVER-CS) Han"    # billed to Hanover master
HAN_CS_RATE     = 8.00                               # fixed $8 for both sides


def _is_hanover_sku(sku_key: str) -> bool:
    """Return True if this SKU is billed to Hanover Insurance Group."""
    return sku_key in HANOVER_SKU_KEYS


# --------------------------------------------------------------------------- #
#  Core invoice engine                                                          #
# --------------------------------------------------------------------------- #

ELIGIBLE_BILLING_TYPES = {"Charge Upon Activation", "Hanover", "Han-CS"}


def _generate_prorated_invoice(
    customer: dict,
    contracts: List[dict],
    billing_year: int,
    billing_month: int,
    catalog_index: dict,
    ovr_index: dict,
    mapping_index: dict,
    cust_map_index: dict,
    full_path_index: dict,
    sku_desc_index: dict,
) -> Optional[dict]:
    """
    Build a prorated invoice for a single customer for the given billing month.
    Returns None if the customer has no qualifying devices.

    A device qualifies if:
      - For Hanover / Han-CS: firstDeviceActivationDate falls within the billing month
      - For CUA: activation date (see below) falls within the billing month
      - the contract is not terminated
      - the device has a resolvable SKU

    CUA activation date rules (applied only when billingType == 'Charge Upon Activation'):
      1. Neither firstDeviceActivationDate nor billingStartDate exists
         → skip (device shows "Never Activated" in MyAdmin)
      2. firstDeviceActivationDate exists AND billingStartDate < firstDeviceActivationDate
         → skip (device already auto-activated; covered by main recurring invoice)
      3. firstDeviceActivationDate exists AND (no billingStartDate OR billingStartDate >= firstDeviceActivationDate)
         → qualify using firstDeviceActivationDate (normal new activation)
      4. No firstDeviceActivationDate, billingStartDate exists and falls in billing month
         → qualify using billingStartDate as the activation date
    """
    customer_id   = str((customer.get("userContact") or {}).get("userCompany", {}).get("id") or "")
    customer_name = (customer.get("userContact") or {}).get("userCompany", {}).get("name") or ""

    # Use the normalised name for all price lookups
    cust_norm = _normalize(customer_name)

    billing_type_local = customer.get("billingType", "")
    is_cua = (billing_type_local == "Charge Upon Activation")

    month_start = date(billing_year, billing_month, 1)
    month_end   = date(billing_year, billing_month, _days_in_month(billing_year, billing_month))

    # ---------------------------------------------------------------------- #
    # Collect qualifying devices from the contract list                        #
    # ---------------------------------------------------------------------- #
    qualifying: List[dict] = []

    for contract in contracts:
        if contract.get("isTerminated"):
            continue

        raw_fcd = (contract.get("firstDeviceActivationDate") or "")[:10]
        raw_bsd = (contract.get("billingStartDate")          or "")[:10]

        if is_cua:
            # ── CUA-specific activation date resolution ──────────────────
            # Rule 1: neither date → skip (Never Activated)
            if not raw_fcd and not raw_bsd:
                continue

            if raw_fcd:
                # Parse firstDeviceActivationDate
                try:
                    fcd = date.fromisoformat(raw_fcd)
                except ValueError:
                    continue

                # Rule 2: billingStartDate exists and predates firstConnectDate
                # → already auto-activated on its own, skip
                if raw_bsd:
                    try:
                        bsd = date.fromisoformat(raw_bsd)
                    except ValueError:
                        bsd = None
                    if bsd and bsd < fcd:
                        continue

                # Rule 3: normal new activation — use firstDeviceActivationDate
                activation_date     = fcd
                raw_activation_date = raw_fcd

            else:
                # Rule 4: no firstConnectDate — use billingStartDate as activation
                try:
                    bsd = date.fromisoformat(raw_bsd)
                except ValueError:
                    continue
                activation_date     = bsd
                raw_activation_date = raw_bsd

            # Only devices activating THIS billing month
            if not (month_start <= activation_date <= month_end):
                continue

        else:
            # ── Hanover / Han-CS: original logic unchanged ────────────────
            if not raw_fcd:
                continue
            try:
                fcd = date.fromisoformat(raw_fcd)
            except ValueError:
                continue
            if not (month_start <= fcd <= month_end):
                continue
            activation_date     = fcd
            raw_activation_date = raw_fcd

        device       = contract.get("device") or {}
        serial       = device.get("serialNumber") or ""
        rate_plan    = (contract.get("promoCode") or "").upper()
        billing_plan = (contract.get("activeDevicePlan") or {}).get("name") or ""

        # Resolve SKU
        sku_key = _resolve_sku(cust_norm, rate_plan, mapping_index, cust_map_index)
        if not sku_key:
            # Try billing plan name as fallback
            sku_key = _resolve_sku(cust_norm, billing_plan, mapping_index, cust_map_index)
        if not sku_key:
            sku_key = "UNMAPPED"

        # Resolve monthly rate
        monthly_rate, price_source = _resolve_price(cust_norm, sku_key, ovr_index, catalog_index)
        if not monthly_rate:
            monthly_rate = 0.0

        days_active, days_in_month, factor = _prorate_factor(activation_date, billing_year, billing_month)
        prorated_charge = round(monthly_rate * factor, 2)

        qualifying.append({
            "serialNumber":        serial,
            "ratePlanCode":        rate_plan,
            "skuKey":              sku_key,
            "monthlyRate":         monthly_rate,
            "priceSource":         price_source,
            "firstConnectDate":    raw_activation_date,
            "firstConnectDateObj": activation_date,
            "daysInMonth":         days_in_month,
            "daysActive":          days_active,
            "prorateFactor":       factor,
            "proratedCharge":      prorated_charge,
            "itemCode":            full_path_index.get(sku_key, sku_key),
            "skuDesc":             sku_desc_index.get(sku_key, sku_key),
        })

    if not qualifying:
        return None

    # ---------------------------------------------------------------------- #
    # For Hanover customers, split devices into two pools:                    #
    #   hanover_pool  — Hanover-billed SKUs  → roll up to Hanover master      #
    #   customer_pool — camera/aux SKUs      → billed directly to customer    #
    # CUA customers always go entirely into customer_pool.                    #
    #                                                                          #
    # For Han-CS customers, each qualifying device generates TWO entries:     #
    #   customer_pool entry → HAN_CS_CUST_SKU @ $8                            #
    #   hanover_pool  entry → HAN_CS_HAN_SKU  @ $8                            #
    # Both entries are prorated from $8 (not the device's mapped rate).       #
    # ---------------------------------------------------------------------- #
    billing_type = customer.get("billingType", "")

    if billing_type == "Han-CS":
        # Each device spawns two entries — one for the customer, one for Hanover
        customer_pool: List[dict] = []
        hanover_pool:  List[dict] = []
        for dev in qualifying:
            fcd = dev["firstConnectDateObj"]
            days_active, days_in_month, factor = _prorate_factor(fcd, billing_year, billing_month)
            # Customer-side entry
            cust_prorated = round(HAN_CS_RATE * factor, 2)
            customer_pool.append({
                **dev,
                "skuKey":         HAN_CS_CUST_SKU,
                "monthlyRate":    HAN_CS_RATE,
                "proratedCharge": cust_prorated,
                "itemCode":       full_path_index.get(HAN_CS_CUST_SKU, HAN_CS_CUST_SKU),
                "skuDesc":        sku_desc_index.get(HAN_CS_CUST_SKU, HAN_CS_CUST_SKU),
                "priceSource":    "hancs_fixed",
                "sectionGroup":   "hancs",
            })
            # Hanover-side entry
            han_prorated = round(HAN_CS_RATE * factor, 2)
            hanover_pool.append({
                **dev,
                "skuKey":         HAN_CS_HAN_SKU,
                "monthlyRate":    HAN_CS_RATE,
                "proratedCharge": han_prorated,
                "itemCode":       full_path_index.get(HAN_CS_HAN_SKU, HAN_CS_HAN_SKU),
                "skuDesc":        sku_desc_index.get(HAN_CS_HAN_SKU, HAN_CS_HAN_SKU),
                "priceSource":    "hancs_fixed",
                "sectionGroup":   "hancs",
            })

    elif billing_type == "Hanover":
        hanover_pool  = [d for d in qualifying if     _is_hanover_sku(d["skuKey"])]
        customer_pool = [d for d in qualifying if not _is_hanover_sku(d["skuKey"])]
        # Tag standard Hanover pool entries with sectionGroup
        for d in hanover_pool:
            d["sectionGroup"] = "hanover"
    else:
        hanover_pool  = []
        customer_pool = qualifying

    # Build invoices for each pool, return list (0-2 items)
    results: List[dict] = []

    # Determine the effective billing type for the customer-side pool
    if billing_type == "Han-CS":
        cust_pool_bt = "Han-CS"
        cust_pool_id = customer_id + "__hancs_cust"
    elif billing_type == "Hanover":
        cust_pool_bt = "Charge Upon Activation"
        cust_pool_id = customer_id + "__cam"
    else:
        cust_pool_bt = billing_type
        cust_pool_id = customer_id

    for pool, pool_billing_type, pool_name, pool_id in [
        (hanover_pool,  "Hanover",    customer_name, customer_id),
        (customer_pool, cust_pool_bt, customer_name, cust_pool_id),
    ]:
        if not pool:
            continue
        inv = _build_invoice_from_pool(
            pool, pool_billing_type, pool_name, pool_id,
            billing_year, billing_month, customer
        )
        if inv:
            results.append(inv)

    if not results:
        return None
    if len(results) == 1:
        return results[0]
    # Return a sentinel list — caller must handle
    return results  # type: ignore


def _build_invoice_from_pool(
    qualifying: List[dict],
    billing_type: str,
    customer_name: str,
    customer_id: str,
    billing_year: int,
    billing_month: int,
    customer: dict,
) -> Optional[dict]:
    """Build a single prorated invoice dict from a pool of qualifying devices."""

    # ---------------------------------------------------------------------- #
    # Group devices by (skuKey, firstConnectDate) — one QB line item per group #
    # ---------------------------------------------------------------------- #
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for dev in qualifying:
        key = (dev["skuKey"], dev["firstConnectDate"])
        groups[key].append(dev)

    last_day_label = _month_last_day_label(billing_year, billing_month)
    month_label    = _month_label(billing_year, billing_month)

    # Next month label for the full-month "forward service" line
    if billing_month == 12:
        next_year, next_month = billing_year + 1, 1
    else:
        next_year, next_month = billing_year, billing_month + 1
    next_month_label = _month_label(next_year, next_month)

    line_items: List[dict] = []

    # Sort groups by firstConnectDate ascending (matches QB invoice order)
    for (sku_key, fcd_str), devs in sorted(groups.items(), key=lambda x: x[0][1]):
        rep     = devs[0]   # all devs in group share same SKU/date/rate
        qty     = len(devs)
        serials = [d["serialNumber"] for d in devs]

        connect_label = _connect_day_label(rep["firstConnectDateObj"])
        description = (
            f"{rep['skuDesc']} - New Activations\n"
            f"Prorated {connect_label} through {last_day_label} for devices:\n"
            + "\n".join(serials)
        )

        line_items.append({
            "type":          "prorated",
            "itemCode":      rep["itemCode"],
            "skuKey":        sku_key,
            "skuDesc":       rep["skuDesc"],
            "description":   description,
            "quantity":      qty,
            "priceEach":     rep["proratedCharge"],   # per-device prorated amount
            "amount":        round(rep["proratedCharge"] * qty, 2),
            "monthlyRate":   rep["monthlyRate"],
            "priceSource":   rep["priceSource"],
            "firstConnectDate": fcd_str,
            "daysActive":    rep["daysActive"],
            "daysInMonth":   rep["daysInMonth"],
            "prorateFactor": rep["prorateFactor"],
            "serials":       serials,
            "taxable":       True,
            "sectionGroup":  rep.get("sectionGroup", "hanover"),
        })

    # ---------------------------------------------------------------------- #
    # "Forward month" full-service line (e.g. "July Service")                 #
    # Covers all newly activated devices at the full monthly rate for the      #
    # next billing cycle. Each distinct SKU gets its own forward line.         #
    # ---------------------------------------------------------------------- #
    forward_groups: Dict[str, List[dict]] = defaultdict(list)
    for dev in qualifying:
        forward_groups[dev["skuKey"]].append(dev)

    for sku_key, devs in sorted(forward_groups.items()):
        rep      = devs[0]
        qty      = len(devs)
        serials  = [d["serialNumber"] for d in devs]
        rate     = rep["monthlyRate"]

        description = f"{rep['skuDesc']} - New Activations {next_month_label} Service"

        line_items.append({
            "type":          "forward",
            "itemCode":      rep["itemCode"],
            "skuKey":        sku_key,
            "skuDesc":       rep["skuDesc"],
            "description":   description,
            "quantity":      qty,
            "priceEach":     rate,
            "amount":        round(rate * qty, 2),
            "monthlyRate":   rate,
            "priceSource":   rep["priceSource"],
            "firstConnectDate": None,
            "daysActive":    None,
            "daysInMonth":   None,
            "prorateFactor": None,
            "serials":       [],    # serials already listed in prorated section
            "taxable":       True,
            "sectionGroup":  rep.get("sectionGroup", "hanover"),
        })

    prorated_total = sum(li["amount"] for li in line_items if li["type"] == "prorated")
    forward_total  = sum(li["amount"] for li in line_items if li["type"] == "forward")
    grand_total    = round(prorated_total + forward_total, 2)

    return {
        "customerId":       customer_id,
        "customerName":     customer_name,
        "billingType":      billing_type,
        "billingMonth":     f"{billing_year}-{billing_month:02d}",
        "billingMonthLabel":month_label,
        "nextMonthLabel":   next_month_label,
        "lineItems":        line_items,
        "proratedTotal":    round(prorated_total, 2),
        "forwardTotal":     round(forward_total, 2),
        "grandTotal":       grand_total,
        "newDeviceCount":   len(qualifying),
        "hasPriceWarnings": any(d["monthlyRate"] == 0 or d["skuKey"] == "UNMAPPED" for d in qualifying),
    }


# keep a reference so callers can check type
_INVOICE_LIST_TYPE = list


# --------------------------------------------------------------------------- #
#  Hanover roll-up: merge all Hanover sub-customer invoices into one          #
# --------------------------------------------------------------------------- #

HANOVER_MASTER_NAME = "Hanover Insurance Group"
HANOVER_MASTER_ID   = "__hanover_master__"


def _merge_prorated_lines(lines_by_key: Dict[tuple, List[dict]]) -> List[dict]:
    """
    Helper: collapse a {(skuKey, fcd): [line_items]} dict into a flat sorted
    list, combining lines that share the same SKU+date (stacking serials, summing
    qty and amount).
    """
    merged: List[dict] = []
    for (sku_key, fcd), lines in sorted(lines_by_key.items(), key=lambda x: (x[0][1], x[0][0])):
        if len(lines) == 1:
            merged.append(lines[0])
            continue
        rep     = lines[0]
        qty     = sum(li["quantity"] for li in lines)
        amount  = round(sum(li["amount"] for li in lines), 2)
        serials = [s for li in lines for s in (li.get("serials") or [])]
        desc_lines   = rep["description"].split("\n")
        header_lines = desc_lines[:2] if len(desc_lines) >= 2 else desc_lines
        description  = "\n".join(header_lines) + "\n" + "\n".join(serials)
        merged.append({
            **rep,
            "quantity":    qty,
            "amount":      amount,
            "serials":     serials,
            "description": description,
        })
    return merged


def _merge_forward_lines(fwd_by_sku: Dict[str, List[dict]]) -> List[dict]:
    """
    Helper: collapse a {skuKey: [forward_line_items]} dict into a flat sorted list,
    summing quantity and recomputing amount.
    """
    merged: List[dict] = []
    for sku_key, fwd_lines in sorted(fwd_by_sku.items()):
        rep    = fwd_lines[0]
        qty    = sum(li["quantity"] for li in fwd_lines)
        amount = round(rep["priceEach"] * qty, 2)
        merged.append({
            **rep,
            "quantity": qty,
            "amount":   amount,
            "serials":  [],
        })
    return merged


def _merge_hanover_invoices(invoices: List[dict]) -> List[dict]:
    """
    All invoices with billingType == 'Hanover' are merged into a single
    master invoice billed to HANOVER_MASTER_NAME.  Non-Hanover invoices
    pass through unchanged.

    The master invoice contains up to four ordered sections, each with its own
    sectionGroup + type combination:

      1. sectionGroup='hanover', type='prorated'  — standard HANOVER prorated
      2. sectionGroup='hanover', type='forward'   — standard HANOVER forward
      3. sectionGroup='hancs',   type='prorated'  — Han-CS prorated
      4. sectionGroup='hancs',   type='forward'   — Han-CS forward

    Han-CS lines originate from devices belonging to Han-CS sub-customers that
    have been converted to HAN_CS_HAN_SKU entries by _generate_prorated_invoice().
    They must NEVER be date-merged with standard HANOVER lines.

    Merging rules (within each section group):
    - Prorated lines are re-merged by (skuKey, firstConnectDate) across
      sub-customers so devices activating on the same day share one row.
    - Forward lines are re-aggregated by skuKey (qty summed).
    """
    hanover_invoices = [inv for inv in invoices if inv.get("billingType") == "Hanover"]
    other_invoices   = [inv for inv in invoices if inv.get("billingType") != "Hanover"]

    if not hanover_invoices:
        return invoices   # nothing to merge

    # Use billing month labels from the first Hanover invoice (all same month)
    ref = hanover_invoices[0]

    # ── Bucket all line items by sectionGroup ─────────────────────────────
    # sectionGroup defaults to 'hanover' for any line that pre-dates this field
    prot_hanover: Dict[tuple, List[dict]] = defaultdict(list)
    prot_hancs:   Dict[tuple, List[dict]] = defaultdict(list)
    fwd_hanover:  Dict[str,   List[dict]] = defaultdict(list)
    fwd_hancs:    Dict[str,   List[dict]] = defaultdict(list)

    for inv in hanover_invoices:
        for li in inv["lineItems"]:
            sg  = li.get("sectionGroup", "hanover")
            fcd = li.get("firstConnectDate") or ""
            if li["type"] == "prorated":
                key = (li["skuKey"], fcd)
                if sg == "hancs":
                    prot_hancs[key].append(li)
                else:
                    prot_hanover[key].append(li)
            else:  # forward
                if sg == "hancs":
                    fwd_hancs[li["skuKey"]].append(li)
                else:
                    fwd_hanover[li["skuKey"]].append(li)

    # ── Build each section ─────────────────────────────────────────────────
    merged_hanover_prorated = _merge_prorated_lines(prot_hanover)
    merged_hanover_forward  = _merge_forward_lines(fwd_hanover)
    merged_hancs_prorated   = _merge_prorated_lines(prot_hancs)
    merged_hancs_forward    = _merge_forward_lines(fwd_hancs)

    # Tag section groups explicitly (in case they were missing)
    for li in merged_hanover_prorated + merged_hanover_forward:
        li["sectionGroup"] = "hanover"
    for li in merged_hancs_prorated + merged_hancs_forward:
        li["sectionGroup"] = "hancs"

    # Ordered: standard HANOVER first, then Han-CS
    all_lines = (
        merged_hanover_prorated
        + merged_hanover_forward
        + merged_hancs_prorated
        + merged_hancs_forward
    )

    prorated_total = round(sum(li["amount"] for li in all_lines if li["type"] == "prorated"), 2)
    forward_total  = round(sum(li["amount"] for li in all_lines if li["type"] == "forward"),  2)

    master_invoice = {
        "customerId":        HANOVER_MASTER_ID,
        "customerName":      HANOVER_MASTER_NAME,
        "billingType":       "Hanover",
        "billingMonth":      ref["billingMonth"],
        "billingMonthLabel": ref["billingMonthLabel"],
        "nextMonthLabel":    ref["nextMonthLabel"],
        "lineItems":         all_lines,
        "proratedTotal":     prorated_total,
        "forwardTotal":      forward_total,
        "grandTotal":        round(prorated_total + forward_total, 2),
        "newDeviceCount":    sum(inv["newDeviceCount"] for inv in hanover_invoices),
        "hasPriceWarnings":  any(inv["hasPriceWarnings"] for inv in hanover_invoices),
    }

    return other_invoices + [master_invoice]


# --------------------------------------------------------------------------- #
#  API Routes                                                                  #
# --------------------------------------------------------------------------- #

@router.get("/invoices/prorated")
async def get_prorated_invoices(
    month: str = Query(
        default="",
        description="Billing month as YYYY-MM (defaults to current month)",
    ),
    billing_type: str = Query(
        default="",
        description="Filter to one billing type ('Charge Upon Activation' or 'Hanover'). "
                    "Leave blank for both.",
    ),
):
    """
    Returns prorated invoice data for all qualifying customers in the given
    billing month. Qualifies customers whose billing type is CUA or Hanover
    and who have ≥1 device with firstDeviceActivationDate in the target month.
    """
    # Parse month ──────────────────────────────────────────────────────────
    if month:
        try:
            parsed  = datetime.strptime(month, "%Y-%m")
            b_year  = parsed.year
            b_month = parsed.month
        except ValueError:
            raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    else:
        today   = date.today()
        b_year  = today.year
        b_month = today.month

    # Determine which billing types to include ─────────────────────────────
    if billing_type:
        if billing_type not in ELIGIBLE_BILLING_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"billing_type must be one of: {sorted(ELIGIBLE_BILLING_TYPES)}",
            )
        wanted_types = {billing_type}
    else:
        wanted_types = ELIGIBLE_BILLING_TYPES

    # Pull data from cache ─────────────────────────────────────────────────
    all_contracts: List[dict] = _sync_cache.get("contracts") or []
    if not all_contracts:
        raise HTTPException(
            status_code=503,
            detail="No contract data cached. Please run a MyAdmin sync first.",
        )

    # Build a map: companyId -> list[contract] ─────────────────────────────
    contracts_by_company: Dict[str, List[dict]] = defaultdict(list)
    for c in all_contracts:
        uc      = c.get("userContact") or {}
        company = uc.get("userCompany") or {}
        cid     = str(company.get("id") or "")
        if cid:
            contracts_by_company[cid].append(c)

    # Load lookup indices (built fresh each request — fast, files are small)
    (catalog_index, ovr_index, mapping_index,
     cust_map_index, full_path_index, sku_desc_index) = _build_indices()

    # Import billing_type lookup from customers module
    from .customers import billing_type_overrides, qb_customers

    invoices: List[dict] = []

    for company_id, company_contracts in contracts_by_company.items():
        if not company_contracts:
            continue

        # Derive billing type — priority: manual override → QB record → Unknown
        raw_name = ((company_contracts[0].get("userContact") or {})
                    .get("userCompany") or {}).get("name") or ""

        bt = (
            billing_type_overrides.get(company_id)
            or (qb_customers.get(_normalize(raw_name)) or {}).get("billingType")
            or "Unknown"
        )

        # ── Safety net: even if the customer isn't labelled Hanover, promote ──
        # ── them to Hanover if ANY contract activating this month carries the ──
        # ── HANOVER promo code — so we never silently drop these devices.     ──
        if bt not in ELIGIBLE_BILLING_TYPES:
            month_start = date(b_year, b_month, 1)
            month_end   = date(b_year, b_month, _days_in_month(b_year, b_month))
            for c in company_contracts:
                if c.get("isTerminated"):
                    continue
                raw_fcd = (c.get("firstDeviceActivationDate") or "")[:10]
                if not raw_fcd:
                    continue
                try:
                    fcd = date.fromisoformat(raw_fcd)
                except ValueError:
                    continue
                if month_start <= fcd <= month_end:
                    promo = (c.get("promoCode") or "").upper()
                    if promo == "HANOVER":
                        bt = "Hanover"
                        break

        if bt not in wanted_types:
            continue

        # Attach billing type so _generate_prorated_invoice can include it
        fake_customer = {
            "userContact": {
                "userCompany": {
                    "id":   company_id,
                    "name": raw_name,
                }
            },
            "billingType": bt,
        }

        invoice = _generate_prorated_invoice(
            customer        = fake_customer,
            contracts       = company_contracts,
            billing_year    = b_year,
            billing_month   = b_month,
            catalog_index   = catalog_index,
            ovr_index       = ovr_index,
            mapping_index   = mapping_index,
            cust_map_index  = cust_map_index,
            full_path_index = full_path_index,
            sku_desc_index  = sku_desc_index,
        )

        if invoice is not None:
            # _generate_prorated_invoice may return a list when a Hanover
            # customer also has camera devices (two separate invoices)
            if isinstance(invoice, list):
                invoices.extend(invoice)
            else:
                invoices.append(invoice)

    # Merge all Hanover sub-customer invoices into one master invoice
    invoices = _merge_hanover_invoices(invoices)

    # Sort by customer name
    invoices.sort(key=lambda x: x["customerName"].lower())

    return {
        "billingMonth":    f"{b_year}-{b_month:02d}",
        "billingMonthLabel": _month_label(b_year, b_month),
        "billingTypes":    sorted(wanted_types),
        "invoiceCount":    len(invoices),
        "totalNewDevices": sum(inv["newDeviceCount"] for inv in invoices),
        "totalProrated":   round(sum(inv["proratedTotal"] for inv in invoices), 2),
        "totalForward":    round(sum(inv["forwardTotal"]  for inv in invoices), 2),
        "grandTotal":      round(sum(inv["grandTotal"]    for inv in invoices), 2),
        "invoices":        invoices,
    }


@router.get("/invoices/prorated/{customer_id}")
async def get_prorated_invoice_for_customer(
    customer_id: str,
    month: str = Query(default="", description="Billing month YYYY-MM"),
):
    """
    Returns the prorated invoice for a single customer for the given month.
    """
    if month:
        try:
            parsed  = datetime.strptime(month, "%Y-%m")
            b_year  = parsed.year
            b_month = parsed.month
        except ValueError:
            raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    else:
        today   = date.today()
        b_year  = today.year
        b_month = today.month

    all_contracts: List[dict] = _sync_cache.get("contracts") or []
    if not all_contracts:
        raise HTTPException(status_code=503, detail="No contract data cached.")

    company_contracts = [
        c for c in all_contracts
        if str(((c.get("userContact") or {}).get("userCompany") or {}).get("id") or "") == customer_id
    ]
    if not company_contracts:
        raise HTTPException(status_code=404, detail=f"No contracts found for customer {customer_id}")

    (catalog_index, ovr_index, mapping_index,
     cust_map_index, full_path_index, sku_desc_index) = _build_indices()

    from .customers import billing_type_overrides, qb_customers

    raw_name = ((company_contracts[0].get("userContact") or {})
                .get("userCompany") or {}).get("name") or ""
    bt = (
        billing_type_overrides.get(customer_id)
        or (qb_customers.get(_normalize(raw_name)) or {}).get("billingType")
        or "Unknown"
    )

    fake_customer = {
        "userContact": {"userCompany": {"id": customer_id, "name": raw_name}},
        "billingType": bt,
    }

    invoice = _generate_prorated_invoice(
        customer        = fake_customer,
        contracts       = company_contracts,
        billing_year    = b_year,
        billing_month   = b_month,
        catalog_index   = catalog_index,
        ovr_index       = ovr_index,
        mapping_index   = mapping_index,
        cust_map_index  = cust_map_index,
        full_path_index = full_path_index,
        sku_desc_index  = sku_desc_index,
    )

    if not invoice:
        return {
            "found":        False,
            "customerId":   customer_id,
            "customerName": raw_name,
            "billingMonth": f"{b_year}-{b_month:02d}",
            "message":      "No devices with a first connect date in this billing month.",
        }

    return {"found": True, **invoice}
