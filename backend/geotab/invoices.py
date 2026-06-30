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
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

# Re-use the same normaliser and price-resolver already used in reconciliation
from .reconciliation import _normalize, _resolve_price

# Shared in-memory cache populated by customers.py sync
from .customers import _sync_cache, _clean_name, _strip_han_cs, _strip_sub_account_suffix, billing_date_overrides, BILLING_DATE_OVERRIDES_FILE, _save_json

# --------------------------------------------------------------------------- #
#  File paths (same dir as all other geotab data files)                        #
# --------------------------------------------------------------------------- #
_HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------- #
#  Persistent stores for user-managed overrides                                 #
# --------------------------------------------------------------------------- #

EXCLUDED_INVOICES_FILE  = os.path.join(_HERE, "excluded_invoices.json")
SKU_OVERRIDES_FILE      = os.path.join(_HERE, "invoice_sku_overrides.json")

def _load_excluded_invoices() -> set:
    """Load the set of excluded invoice keys (customerId|billingMonth)."""
    import json
    try:
        with open(EXCLUDED_INVOICES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()

def _save_excluded_invoices(keys: set) -> None:
    _save_json(EXCLUDED_INVOICES_FILE, sorted(keys))

def _load_sku_overrides() -> dict:
    """Load SKU override map {customerId|billingMonth|serial -> skuKey}."""
    import json
    try:
        with open(SKU_OVERRIDES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_sku_overrides(overrides: dict) -> None:
    _save_json(SKU_OVERRIDES_FILE, overrides)

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


def _bill_to_address(qb_record: dict, customer_name: str = "") -> List[str]:
    """Return address lines for the Bill To block from a QB customer record.

    QB exports free-form address lines in columns 'Bill to 1'–'Bill to 5'.
    Bill to 1 is typically the company name repeated — skip it when it
    matches the invoice customer name (already shown in bold above the block).
    Only return non-empty lines after that filter.
    """
    if not qb_record:
        return []

    raw_lines = [
        qb_record.get("billTo1", ""),
        qb_record.get("billTo2", ""),
        qb_record.get("billTo3", ""),
        qb_record.get("billTo4", ""),
        qb_record.get("billTo5", ""),
    ]

    # Normalise customer name for comparison (strip sub-account suffix after ":")
    cust_bare = customer_name.split(":")[-1].strip().lower()

    lines: List[str] = []
    for i, line in enumerate(raw_lines):
        line = line.strip()
        if not line:
            continue
        # Skip Bill to 1 or Bill to 2 when it's just a repeat of the customer name
        if i <= 1 and (line.lower() == cust_bare or line.lower() == customer_name.strip().lower()):
            continue
        lines.append(line)
    return lines


def _safe_date(raw) -> str:
    """
    Slice an ISO datetime string to yyyy-mm-dd and return empty string for:
      - None / empty
      - .NET DateTime.MinValue sentinel "0001-01-01..." that MyAdmin returns
        when a date field has no value set.
    """
    s = (raw or "")[:10]
    return "" if (not s or s.startswith("0001")) else s


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
      catalog_index    : skuKey -> defaultPrice
      ovr_index        : (norm_customerName, skuKey) -> override price
      mapping_index    : ratePlanCode_upper -> skuKey   (global, first-entry-wins)
      cust_map_index   : (norm_customerName, ratePlanCode_upper) -> skuKey
      full_path_index  : skuKey -> fullPath  (QB item code format)
      sku_desc_index   : skuKey -> desc  (human label for description)
      category_index   : skuKey -> category  (used to exclude non-billable categories)
      plan_promo_index : (planLevel_upper, ratePlanCode_upper) -> skuKey  (Tier 1.5)
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
    category_index: Dict[str, str] = {
        s["skuKey"]: s.get("category") or ""
        for s in catalog
    }
    ovr_index: Dict[tuple, float] = {
        (_normalize(o["customerName"]), o["skuKey"]): float(o.get("price") or 0)
        for o in overrides
    }
    # First-entry-wins: ensures the GO CORE variant is the safe flat default
    # for codes like BUNDLE-GO that appear multiple times with different planLevels.
    mapping_index: Dict[str, str] = {}
    for m in mappings:
        key = (m.get("ratePlanCode") or "").upper()
        if key and key not in mapping_index:
            mapping_index[key] = m.get("skuKey") or ""
    cust_map_index: Dict[tuple, str] = {
        (_normalize(m["customerName"]), (m.get("ratePlanCode") or "").upper()): m.get("skuKey") or ""
        for m in cust_maps
    }
    # Tier 1.5 compound index: (planLevel_upper, ratePlanCode_upper) -> skuKey
    # Used to disambiguate codes like SWELL-NOINS3 that resolve differently on
    # GO vs GO EXPAND billing plans.
    plan_promo_index: Dict[tuple, str] = {
        (
            (m.get("planLevel") or "").upper(),
            (m.get("ratePlanCode") or "").upper(),
        ): m.get("skuKey") or ""
        for m in mappings
        if m.get("planLevel")
    }

    return catalog_index, ovr_index, mapping_index, cust_map_index, full_path_index, sku_desc_index, category_index, plan_promo_index


def _resolve_sku(customer_norm: str, rate_plan_code: str,
                 mapping_index: dict, cust_map_index: dict,
                 billing_plan: str = "",
                 plan_promo_index: Optional[dict] = None) -> Optional[str]:
    """
    Tiered SKU resolution (mirrors reconciliation.py):
      Tier 1:   customer-specific mapping on ratePlanCode
      Tier 1.5: (planLevel, ratePlanCode) compound lookup — disambiguates
                same promoCode across GO / GO EXPAND billing plans
      Tier 2:   global flat mapping on ratePlanCode
      Returns None if no mapping found.
    """
    code = (rate_plan_code or "").upper()
    # Tier 1: customer override
    sku = cust_map_index.get((customer_norm, code))
    if sku:
        return sku
    # Tier 1.5: plan + promoCode compound
    if plan_promo_index and billing_plan and code:
        sku = plan_promo_index.get((billing_plan.upper(), code))
        if sku:
            return sku
    # Tier 2: global flat
    return mapping_index.get(code) or None


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


# SKU categories that are never billed on prorated invoices.
# Digital Matter devices are billed separately by the DM billing system;
# including them here would double-bill the customer.
EXCLUDED_CATEGORIES = {"Digital Matter Service", "Digital Matter Equipment"}

# Serial number prefixes that identify Digital Matter hardware.
# These devices must NEVER appear on prorated invoices regardless of what
# SKU/plan they resolve to.  DM serials can slip through the category filter
# when their activeDevicePlan is "PRO MODE" (resolves to Service Fee Geotab (Pro),
# a non-DM category) rather than a recognised DM plan name.
DM_SERIAL_PREFIXES: tuple = (
    "CN", "JQ", "HN", "C1", "CL", "DC", "CY", "OE", "OB", "OF", "OG",
    # EG and EK are Phillips Connect devices — NOT Digital Matter.
    # They bill to 'Phillips Connect Service:Tracking Fee' at $14.95.
    # Removed from this list so they pass through to prorated invoices.
)

# Surfsight camera serial prefix — always resolves to SS Service Fee on
# prorated invoices regardless of billing_plan or promoCode.
SURFSIGHT_SERIAL_PREFIX = "EVD-MKH-SRF"

def _is_dm_serial(serial: str) -> bool:
    """Return True if the serial number belongs to a Digital Matter device."""
    s = (serial or "").strip().upper()
    return s.startswith(DM_SERIAL_PREFIXES)


# Serial-prefix → SKU fallback for OEM hardware ONLY.
# Used as a last resort when neither activeDevicePlan.name nor promoCode
# resolves to a known SKU (e.g. plan name variant not yet in sku_mappings.json).
# Ordered longest-prefix first within each manufacturer so startswith() matches
# the most specific prefix before a shorter one.
#
# This table covers OEM manufacturers ONLY — where the serial prefix uniquely
# identifies both the hardware type AND the correct billing SKU regardless of
# what plan MyAdmin shows.
#
# Geotab hardware (GA, G9, G8, G7, X1, X2, B1, B2, GE, GF, etc.) is
# intentionally NOT listed here — those devices can be on any service plan
# (Pro, ProPlus, Asset, Suspend, GO Focus Plus, etc.) and must resolve via
# billing_plan / promoCode exclusively.
#
# OEM manufacturers:
#   Ford        DW          → Geotab Ford (Premium (OEM))
#   GM          CO          → Geotab GM (Premium (OEM))
#   Mack        DY          → Geotab Mack (Premium (OEM))
#   Volvo       D8          → Geotab Volvo (Premium (OEM))
#   CAT         D5, DS      → CAT AEMP (OEM)
#   John Deere  DM          → John Deere AEMP (OEM)
#   Komatsu     JL          → Komatsu AEMP (OEM)
#   CalAmp      C3          → Service Fee CalAmp (Asset)
#   Phillips     EG, EK      → Tracking Fee (Phillips Connect Service Fee)
#   Hitachi     P8          → (no catalog entry yet — falls through to UNMAPPED)
_SERIAL_PREFIX_SKU: list = [
    # Longer/more-specific prefixes first
    ("EVD-MKH-SRF", "SS Service Fee"),   # Surfsight cameras
    ("DS", "CAT AEMP (OEM)"),
    ("D5", "CAT AEMP (OEM)"),
    ("DM", "John Deere AEMP (OEM)"),
    ("DW", "Geotab Ford (Premium (OEM))"),
    ("DY", "Geotab Mack (Premium (OEM))"),
    ("D8", "Geotab Volvo (Premium (OEM))"),
    ("CO", "Geotab GM (Premium (OEM))"),
    ("JL", "Komatsu AEMP (OEM)"),
    ("C3", "Service Fee CalAmp (Asset)"),
    ("EG", "Tracking Fee"),
    ("EK", "Tracking Fee"),
]

# GO Focus (GF) / GO Focus Plus (GE) serial-prefix base SKUs.
# Used by _gf_ge_base_sku() when a device has NO promoCode and its billing plan
# does not disambiguate further (e.g. both map to "GO EXPAND" in MyAdmin).
_GE_GF_BASE_SKU: dict = {
    "GE": "Geotab Service (GO Focus Plus)",
    "GF": "Service Fee (GO Focus)",
}

# Post-resolution remap for GF serials: if promoCode resolution (Tier 1.5)
# returns a GO Focus Plus SKU, replace it with the correct GO Focus equivalent.
# GE serials are already correct (GO Focus Plus).
# Keys: GO Focus Plus skuKey   Values: correct GO Focus skuKey for GF hardware
_GF_SKU_REMAP: dict = {
    "Geotab Service (GO Focus Plus)":   "Service Fee (GO Focus)",
    "Service (GO Focus Plus) SW-SI3":   "Service Fee (GO Focus) SW-SI3",
    # SWELL-NOINSTALL / SWELL-NOINSTALL2: no GF-specific STRD variant exists
    # → fall back to base GO Focus service fee
    "Service (GO Focus Plus) SW-STRD":  "Service Fee (GO Focus)",
    "Service (GO Focus Plus) SWELL3":   "Service Fee (GO Focus) SWELL3",
    "Service (GO Focus Plus) Bundle":   "Service Fee (GO Focus)",
}

def _sku_from_serial(serial: str) -> Optional[str]:
    """
    Return a best-guess skuKey based on serial number prefix for OEM hardware,
    or None if the prefix is not in the OEM table.

    This is ONLY for OEM devices whose serial prefix uniquely identifies the
    correct billing SKU (e.g. DW=Ford, CO=GM).  Geotab hardware (X1, X2,
    GA, G9, etc.) is intentionally excluded — those devices can be on any
    service plan and must resolve via billing_plan/promoCode.
    """
    s = (serial or "").strip().upper()
    for prefix, sku_key in _SERIAL_PREFIX_SKU:
        if s.startswith(prefix):
            return sku_key
    return None


# Line-item sort tier based on the serial prefix of the first device in the group.
# Tier 1 — Geotab GO devices  (GA, G9, G8, G7, X1, X2)
# Tier 2 — Geotab Cameras     (GE, GF)
# Tier 3 — Geotab Asset       (B1, B2)
# Tier 4 — Everything else    (OEM, CalAmp, unmapped, …)
_TIER1_PREFIXES = ("GA", "G9", "G8", "G7", "X1", "X2")
_TIER2_PREFIXES = ("GE", "GF")
_TIER3_PREFIXES = ("B1", "B2")

def _serial_tier(serial_upper: str) -> int:
    """Return the sort tier (1-4) for a device serial number."""
    if serial_upper.startswith(_TIER1_PREFIXES):
        return 1
    if serial_upper.startswith(_TIER2_PREFIXES):
        return 2
    if serial_upper.startswith(_TIER3_PREFIXES):
        return 3
    return 4


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
    category_index: dict,
    sku_overrides: Optional[dict] = None,
    plan_promo_index: Optional[dict] = None,
) -> Optional[dict]:
    """
    Build a prorated invoice for a single customer for the given billing month.
    Returns None if the customer has no qualifying devices.

    A device qualifies if its activation date falls within the billing month,
    it is not terminated, and it has a resolvable SKU.

    Activation date rules (applied to ALL billing types):
      1. Neither firstDeviceActivationDate nor billingStartDate exists
         a. startDate ("Assignment Date") exists → use it as billingStartDate (→ Rule 4)
         b. startDate also absent → skip (device shows "Never Activated" in MyAdmin)
      2. firstDeviceActivationDate exists AND billingStartDate < firstDeviceActivationDate
         → skip (already auto-activated; covered by main recurring invoice)
      3. firstDeviceActivationDate exists AND (no billingStartDate OR billingStartDate >= firstDeviceActivationDate)
         → qualify using firstDeviceActivationDate (normal new activation)
      4. No firstDeviceActivationDate, billingStartDate (or startDate fallback) exists
         and falls in billing month → qualify using that date as the activation date
    """
    customer_id   = str((customer.get("userContact") or {}).get("userCompany", {}).get("id") or "")
    customer_name = (customer.get("userContact") or {}).get("userCompany", {}).get("name") or ""

    # Use the normalised name for all price lookups
    cust_norm = _normalize(customer_name)

    month_start = date(billing_year, billing_month, 1)
    month_end   = date(billing_year, billing_month, _days_in_month(billing_year, billing_month))

    # ---------------------------------------------------------------------- #
    # Collect qualifying devices from the contract list                        #
    # ---------------------------------------------------------------------- #
    qualifying: List[dict] = []

    for contract in contracts:
        if contract.get("isTerminated"):
            continue

        # ── Skip never-activated devices ───────────────────────────────────
        # MyAdmin marks never-activated devices with activeDevicePlan.name =
        # "NEVER ACTIVATED" (or blank).  These devices have a startDate
        # ("Assignment Date") but no firstDeviceActivationDate or
        # billingStartDate.  Rule 1a below would otherwise use startDate as a
        # billing activation date, incorrectly placing them on prorated
        # invoices before the device has ever connected.
        _adp_name = ((contract.get("activeDevicePlan") or {}).get("name") or "").upper()
        if not _adp_name or _adp_name == "NEVER ACTIVATED" or "never" in _adp_name:
            continue

        # ── Manual billing-date override (highest priority) ────────────────
        # If the user has set a date for this serial via the UI, use it as
        # billingStartDate and bypass the API dates entirely.
        device_serial_raw = (contract.get("device") or {}).get("serialNumber") or ""
        override_bsd = billing_date_overrides.get(device_serial_raw.strip().upper())

        raw_fcd = _safe_date(contract.get("firstDeviceActivationDate"))
        # If override present it replaces billingStartDate; firstConnectDate
        # from the API is preserved so Rule 2 still applies.
        raw_bsd = override_bsd or _safe_date(contract.get("billingStartDate"))

        # Rule 1a: neither date → try startDate ("Assignment Date" in MyAdmin)
        # as a last-resort activation date for active devices that have no
        # firstDeviceActivationDate and no billingStartDate.
        if not raw_fcd and not raw_bsd:
            raw_sd = _safe_date(contract.get("startDate"))
            if raw_sd:
                raw_bsd = raw_sd   # treat it exactly like billingStartDate (Rule 4 below)
            else:
                continue           # Rule 1b: truly no date at all → skip (Never Activated)

        if raw_fcd:
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

        device       = contract.get("device") or {}
        serial       = device.get("serialNumber") or ""
        rate_plan    = (contract.get("promoCode") or "").upper()
        billing_plan = (contract.get("activeDevicePlan") or {}).get("name") or ""

        # Hard-exclude Digital Matter devices by serial prefix — these are billed
        # through the DM billing system and must never appear on prorated invoices,
        # even if their plan resolves to a non-DM SKU (e.g. "PRO MODE").
        if _is_dm_serial(serial):
            continue

        # Hard-exclude PILOT devices — any rate plan code containing "PILOT"
        # means the customer is trialling those devices and must not be billed.
        if "PILOT" in rate_plan:
            continue

        # Resolve SKU — priority order:
        #   1. Serial-prefix check (HIGHEST for OEM + Surfsight hardware)
        #      DW/CO/DY/D8/EVD-MKH-SRF/etc. always billed to their own SKU
        #      regardless of billing_plan or promoCode.
        #   1.5 GE/GF serial + no promoCode → base hardware SKU
        #      GE (GO Focus Plus) → Geotab Service (GO Focus Plus)
        #      GF (GO Focus)      → Service Fee (GO Focus)
        #   2. Customer-specific mapping on promoCode  (Tier 1)
        #   2.5 Plan+promoCode compound lookup         (Tier 1.5)
        #      e.g. SWELL-NOINS3 on GO Expand → Service (GO Focus Plus) SW-SI3
        #           SWELL-NOINS3 on GO        → Geotab Service (GO SW-SI3)
        #   3. Global mapping on promoCode             (Tier 3)
        #   4. Customer-specific / global on billing_plan
        #   5. UNMAPPED fallback
        #   6. GF serial post-correction: if resolution yielded a GO Focus Plus
        #      SKU but the device is GF hardware, remap to GO Focus equivalent.
        serial_upper  = serial.strip().upper() if serial else ""
        _gf_serial    = serial_upper.startswith("GF")
        _ge_serial    = serial_upper.startswith("GE")

        # Step 1.5: GE/GF with no promoCode → base hardware SKU directly
        if not rate_plan and (_gf_serial or _ge_serial):
            sku_key = _GE_GF_BASE_SKU["GF" if _gf_serial else "GE"]
        else:
            sku_key = (
                _sku_from_serial(serial)
                or _resolve_sku(cust_norm, rate_plan,    mapping_index, cust_map_index,
                                billing_plan, plan_promo_index)
                or _resolve_sku(cust_norm, billing_plan, mapping_index, cust_map_index)
                or "UNMAPPED"
            )
            # Step 6: GF serial post-correction — remap Focus Plus → Focus
            if _gf_serial and sku_key in _GF_SKU_REMAP:
                sku_key = _GF_SKU_REMAP[sku_key]

        # Apply per-serial SKU override (user-managed via the UI)
        billing_month_str = f"{billing_year}-{billing_month:02d}"
        if sku_overrides:
            ovr_key = f"{customer_id}|{billing_month_str}|{serial_upper}"
            if ovr_key in sku_overrides:
                sku_key = sku_overrides[ovr_key]

        # Skip categories that are never prorated here (e.g. Digital Matter —
        # those devices are billed through the DM billing system, not GeoBridge)
        sku_category = category_index.get(sku_key, "")
        if sku_category in EXCLUDED_CATEGORIES:
            continue

        # Resolve monthly rate
        monthly_rate, price_source = _resolve_price(cust_norm, sku_key, ovr_index, catalog_index)
        if not monthly_rate:
            monthly_rate = 0.0

        days_active, days_in_month, factor = _prorate_factor(activation_date, billing_year, billing_month)
        prorated_charge = round(monthly_rate * factor, 2)

        qualifying.append({
            "serialNumber":        serial,
            "serialUpper":         serial_upper,
            "serialTier":          _serial_tier(serial_upper),
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
            "skuOverridden":       bool(sku_overrides and f"{customer_id}|{billing_month_str}|{serial_upper}" in sku_overrides),
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
        # Han-CS split rules:
        #   GO devices (GA/G9/G8/G7/X1/X2) → dual entry: $8 to customer + $8 to Hanover
        #   Everything else (cameras GE/GF, asset B1/B2, OEM, CalAmp, etc.) →
        #     customer_pool only, billed at the device's real resolved SKU and rate.
        #     These are NEVER sent to the Hanover master invoice.
        customer_pool: List[dict] = []
        hanover_pool:  List[dict] = []
        for dev in qualifying:
            serial_upper = dev.get("serialUpper", "")
            if serial_upper.startswith(_TIER1_PREFIXES):
                # GO device — apply dual $8 Han-CS treatment
                fcd = dev["firstConnectDateObj"]
                days_active, days_in_month, factor = _prorate_factor(fcd, billing_year, billing_month)
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
            else:
                # Non-GO device (camera, asset, OEM, CalAmp, …) — customer only,
                # billed at the device's real resolved SKU and rate, no Hanover copy.
                customer_pool.append(dev)

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

    # Sort prorated groups: tier (Geotab GO → Cameras → Asset → Other),
    # then alphabetically by SKU name within each tier, then date ascending.
    # Tier is taken from the lowest (best) tier among devices in the group.
    def _group_sort_key(item):
        (sku_key, fcd_str), devs = item
        tier = min(d.get("serialTier", 4) for d in devs)
        return (tier, sku_key, fcd_str or "")

    for (sku_key, fcd_str), devs in sorted(groups.items(), key=_group_sort_key):
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
            "serialsUpper":  [d["serialUpper"] for d in devs],
            "skuOverridden": any(d.get("skuOverridden") for d in devs),
            "taxable":       True,
            "sectionGroup":  rep.get("sectionGroup", "hanover"),
            "serialTier":    min(d.get("serialTier", 4) for d in devs),
        })

    # ---------------------------------------------------------------------- #
    # "Forward month" full-service line (e.g. "July Service")                 #
    # Covers all newly activated devices at the full monthly rate for the      #
    # next billing cycle. Each distinct SKU gets its own forward line.         #
    # ---------------------------------------------------------------------- #
    forward_groups: Dict[str, List[dict]] = defaultdict(list)
    for dev in qualifying:
        forward_groups[dev["skuKey"]].append(dev)

    def _fwd_sort_key(item):
        sku_key, devs = item
        tier = min(d.get("serialTier", 4) for d in devs)
        return (tier, sku_key)

    for sku_key, devs in sorted(forward_groups.items(), key=_fwd_sort_key):
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
            "serialTier":    min(d.get("serialTier", 4) for d in devs),
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
    for (sku_key, fcd), lines in sorted(lines_by_key.items(), key=lambda x: (min(li.get("serialTier", 4) for li in x[1]), x[0][0], x[0][1] or "")):
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
    for sku_key, fwd_lines in sorted(fwd_by_sku.items(), key=lambda x: (min(li.get("serialTier", 4) for li in x[1]), x[0])):
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
#  Same-customer merge: combine sub-account invoices (e.g. {Cameras}) into    #
#  one invoice per base customer name                                          #
# --------------------------------------------------------------------------- #

def _base_customer_name(name: str) -> str:
    """
    Strip MyAdmin sub-account suffixes so we can group invoices that belong
    to the same physical customer.

    Examples
    --------
    "Hoopaugh Grading Company LLC {Cameras}"  → "Hoopaugh Grading Company LLC"
    "ACES Controls LLC {Han-CS}"              → "ACES Controls LLC"
    "City of Raleigh"                         → "City of Raleigh"
    """
    # Remove any {...} token at the end (handles {Cameras}, {Han-CS}, etc.)
    cleaned = re.sub(r'\s*\{[^}]+\}\s*$', '', name).strip()
    return cleaned or name


def _merge_same_customer_invoices(invoices: List[dict]) -> List[dict]:
    """
    Group invoices that belong to the same base customer (i.e. differ only by a
    {Cameras} / {Han-CS} / similar suffix) into one combined invoice.

    Hanover invoices are never touched here — they've already been merged by
    _merge_hanover_invoices().

    Merge rules
    -----------
    - Line items are concatenated in sub-account order (base account first,
      then suffixed accounts alphabetically).
    - Totals are recomputed from the merged line items.
    - newDeviceCount is summed.
    - QB metadata (billToAddress, terms, qbClass, customerId) is taken from
      whichever sub-invoice has the richest data (non-empty value wins).
    - The merged invoice uses the base customer name (suffix stripped).
    """
    # Separate Hanover invoices — never touch them here
    hanover = [inv for inv in invoices if inv.get("billingType") == "Hanover"]
    others  = [inv for inv in invoices if inv.get("billingType") != "Hanover"]

    # Group by (base_name, billingMonth) — keep insertion order
    groups: Dict[tuple, List[dict]] = {}
    for inv in others:
        base = _base_customer_name(inv["customerName"])
        key  = (base.lower(), inv.get("billingMonth", ""))
        groups.setdefault(key, []).append(inv)

    merged_others: List[dict] = []
    for (base_lower, _bm), group in groups.items():
        if len(group) == 1:
            # Only one sub-account — just normalise the name if it had a suffix
            single = group[0]
            base   = _base_customer_name(single["customerName"])
            if base != single["customerName"]:
                single = {**single, "customerName": base}
            merged_others.append(single)
            continue

        # Sort: base account (no suffix) first, then alphabetical by full name
        group.sort(key=lambda x: (bool(re.search(r'\{[^}]+\}', x["customerName"])),
                                   x["customerName"]))

        # Concatenate all line items, then re-merge to collapse duplicate
        # prorated (same skuKey+date) and forward (same skuKey) lines that
        # originated from different sub-accounts (e.g. base + {Cameras}).
        raw_lines: List[dict] = []
        for inv in group:
            raw_lines.extend(inv.get("lineItems") or [])

        # Re-merge prorated lines by (skuKey, firstConnectDate)
        prot_by_key: Dict[tuple, List[dict]] = defaultdict(list)
        for li in raw_lines:
            if li["type"] == "prorated":
                prot_by_key[(li["skuKey"], li.get("firstConnectDate") or "")].append(li)
        merged_prorated = _merge_prorated_lines(prot_by_key)

        # Re-merge forward lines by skuKey
        fwd_by_sku: Dict[str, List[dict]] = defaultdict(list)
        for li in raw_lines:
            if li["type"] == "forward":
                fwd_by_sku[li["skuKey"]].append(li)
        merged_forward = _merge_forward_lines(fwd_by_sku)

        all_lines = merged_prorated + merged_forward

        prorated_total = round(sum(li["amount"] for li in all_lines if li["type"] == "prorated"), 2)
        forward_total  = round(sum(li["amount"] for li in all_lines if li["type"] == "forward"),  2)

        # Use the first invoice as the base; pick best QB metadata across group
        ref = group[0]
        base_name = _base_customer_name(ref["customerName"])

        def _best(field: str):
            """Return the first non-empty value for `field` across group."""
            for inv in group:
                v = inv.get(field)
                if v:
                    return v
            return ref.get(field, "")

        merged_inv = {
            **ref,
            "customerName":     base_name,
            "lineItems":        all_lines,
            "proratedTotal":    prorated_total,
            "forwardTotal":     forward_total,
            "grandTotal":       round(prorated_total + forward_total, 2),
            "newDeviceCount":   sum(inv.get("newDeviceCount", 0) for inv in group),
            "hasPriceWarnings": any(inv.get("hasPriceWarnings") for inv in group),
            "billToAddress":    _best("billToAddress"),
            "terms":            _best("terms"),
            "qbClass":          _best("qbClass"),
        }
        merged_others.append(merged_inv)

    return hanover + merged_others


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
     cust_map_index, full_path_index, sku_desc_index,
     category_index, plan_promo_index) = _build_indices()

    # Load per-invoice exclusion set and per-serial SKU overrides
    excluded_invoices = _load_excluded_invoices()
    sku_overrides     = _load_sku_overrides()

    # Import billing_type lookup from customers module
    from .customers import billing_type_overrides, qb_customers

    invoices: List[dict] = []

    for company_id, company_contracts in contracts_by_company.items():
        if not company_contracts:
            continue

        # Derive billing type — mirrors enrich_customer() priority chain:
        # (helpers _clean_name, _strip_han_cs, _strip_sub_account_suffix imported at module top)
        #   1. Manual override
        #   2. QB record — looked up via the same suffix-stripped name that
        #      enrich_customer() uses, so Han-CS and sub-account names resolve
        #      to their parent QB entry correctly
        #   3. {Han-CS} suffix in the MyAdmin name → "Han-CS"
        #   4. Unknown
        raw_name = ((company_contracts[0].get("userContact") or {})
                    .get("userCompany") or {}).get("name") or ""
        clean_name = _clean_name(raw_name)

        # Strip suffixes exactly as enrich_customer() does
        _after_sub     = _strip_sub_account_suffix(clean_name)
        qb_lookup_name = _strip_han_cs(_after_sub)
        # Detect Han-CS: check the intermediate value (after strip_sub_account_suffix
        # but before strip_han_cs) — strip_sub now preserves and strips second suffixes
        # so '{Han-CS} {Cameras}' becomes '{Han-CS}' at this stage.
        is_han_cs = _after_sub.strip().lower().endswith("{han-cs}")

        bt = (
            billing_type_overrides.get(company_id)
            or (qb_customers.get(_normalize(qb_lookup_name)) or {}).get("billingType")
            or ("Han-CS" if is_han_cs else None)
            or "Unknown"
        )

        # ── Safety net: even if the customer isn't labelled Hanover, promote ──
        # ── them to Hanover if ANY contract activating this month carries the ──
        # ── HANOVER promo code — so we never silently drop these devices.     ──
        # ── Han-CS customers are deliberately excluded: their name already    ──
        # ── resolved to "Han-CS" above and must never be promoted to "Hanover"──
        if bt not in ELIGIBLE_BILLING_TYPES:
            month_start = date(b_year, b_month, 1)
            month_end   = date(b_year, b_month, _days_in_month(b_year, b_month))
            for c in company_contracts:
                if c.get("isTerminated"):
                    continue
                raw_fcd = _safe_date(c.get("firstDeviceActivationDate"))
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

        # Attach billing type so _generate_prorated_invoice can include it.
        # Build display name from _after_sub (already has the second brace suffix
        # stripped by _strip_sub_account_suffix), then strip {Han-CS} from that.
        # Using clean_name here is wrong for double-suffix accounts like
        # "Acme {Han-CS} {Cameras}": _strip_han_cs on the raw name would only remove
        # {Han-CS} in the middle, leaving the display as "Acme {Cameras}" — which
        # _base_customer_name can normalise but the merge key still differs from the
        # parent account's "Acme", and the invoice title shows the wrong name.
        # Using _after_sub instead ensures both "Acme {Han-CS}" (parent) and
        # "Acme {Han-CS} {Cameras}" (sub) produce display_name = "Acme".
        display_name = _strip_han_cs(_after_sub)
        fake_customer = {
            "userContact": {
                "userCompany": {
                    "id":   company_id,
                    "name": display_name,
                }
            },
            "billingType": bt,
        }

        invoice = _generate_prorated_invoice(
            customer         = fake_customer,
            contracts        = company_contracts,
            billing_year     = b_year,
            billing_month    = b_month,
            catalog_index    = catalog_index,
            ovr_index        = ovr_index,
            mapping_index    = mapping_index,
            cust_map_index   = cust_map_index,
            full_path_index  = full_path_index,
            sku_desc_index   = sku_desc_index,
            category_index   = category_index,
            sku_overrides    = sku_overrides,
            plan_promo_index = plan_promo_index,
        )

        if invoice is not None:
            # Attach billing address + terms from QB customer record
            qb_rec  = qb_customers.get(_normalize(qb_lookup_name)) or {}
            addr    = _bill_to_address(qb_rec, display_name)
            terms   = qb_rec.get("terms", "")
            qbClass = qb_rec.get("qbClass", "")
            # _generate_prorated_invoice may return a list when a Hanover
            # customer also has camera devices (two separate invoices)
            if isinstance(invoice, list):
                for inv in invoice:
                    inv["billToAddress"] = addr
                    inv["terms"]         = terms
                    inv["qbClass"]       = qbClass
                invoices.extend(invoice)
            else:
                invoice["billToAddress"] = addr
                invoice["terms"]         = terms
                invoice["qbClass"]       = qbClass
                invoices.append(invoice)

    # Merge all Hanover sub-customer invoices into one master invoice
    invoices = _merge_hanover_invoices(invoices)

    # Merge sub-account invoices for the same customer (e.g. {Cameras}) into one
    invoices = _merge_same_customer_invoices(invoices)

    # Filter out invoices the user has explicitly excluded (e.g. trial customers)
    billing_month_key = f"{b_year}-{b_month:02d}"
    invoices = [
        inv for inv in invoices
        if f"{inv['customerId']}|{billing_month_key}" not in excluded_invoices
    ]

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
     cust_map_index, full_path_index, sku_desc_index,
     category_index, plan_promo_index) = _build_indices()

    sku_overrides = _load_sku_overrides()

    from .customers import billing_type_overrides, qb_customers

    raw_name   = ((company_contracts[0].get("userContact") or {})
                  .get("userCompany") or {}).get("name") or ""
    clean_name = _clean_name(raw_name)
    _after_sub_s   = _strip_sub_account_suffix(clean_name)
    qb_lookup_name = _strip_han_cs(_after_sub_s)
    is_han_cs  = _after_sub_s.strip().lower().endswith("{han-cs}")
    bt = (
        billing_type_overrides.get(customer_id)
        or (qb_customers.get(_normalize(qb_lookup_name)) or {}).get("billingType")
        or ("Han-CS" if is_han_cs else None)
        or "Unknown"
    )
    # Same fix as bulk endpoint: use _after_sub_s (second suffix already stripped)
    # so that double-suffix names like "Acme {Han-CS} {Cameras}" resolve correctly.
    display_name = _strip_han_cs(_after_sub_s)

    fake_customer = {
        "userContact": {"userCompany": {"id": customer_id, "name": display_name}},
        "billingType": bt,
    }

    invoice = _generate_prorated_invoice(
        customer         = fake_customer,
        contracts        = company_contracts,
        billing_year     = b_year,
        billing_month    = b_month,
        catalog_index    = catalog_index,
        ovr_index        = ovr_index,
        mapping_index    = mapping_index,
        cust_map_index   = cust_map_index,
        full_path_index  = full_path_index,
        sku_desc_index   = sku_desc_index,
        category_index   = category_index,
        sku_overrides    = sku_overrides,
        plan_promo_index = plan_promo_index,
    )

    if not invoice:
        return {
            "found":        False,
            "customerId":   customer_id,
            "customerName": display_name,
            "billingMonth": f"{b_year}-{b_month:02d}",
            "message":      "No devices with a first connect date in this billing month.",
        }

    # Attach billing address + terms + QB class from QB customer record
    qb_rec = qb_customers.get(_normalize(qb_lookup_name)) or {}
    invoice["billToAddress"] = _bill_to_address(qb_rec, display_name)
    invoice["terms"]         = qb_rec.get("terms", "")
    invoice["qbClass"]       = qb_rec.get("qbClass", "")

    return {"found": True, **invoice}


# --------------------------------------------------------------------------- #
#  Invoice exclusion endpoints                                                  #
#  POST   /invoices/exclude/{customer_id}?month=YYYY-MM  → exclude invoice     #
#  DELETE /invoices/exclude/{customer_id}?month=YYYY-MM  → restore invoice     #
#  GET    /invoices/excluded                             → list all keys        #
# --------------------------------------------------------------------------- #

from pydantic import BaseModel as _BaseModel

class _SkuOverrideBody(_BaseModel):
    serial:  str
    skuKey:  str
    month:   str   # YYYY-MM


@router.post("/invoices/exclude/{customer_id}")
async def exclude_invoice(
    customer_id: str,
    month: str = Query(..., description="Billing month as YYYY-MM"),
):
    """Mark a prorated invoice as excluded so it won't appear in the invoice list."""
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    keys = _load_excluded_invoices()
    keys.add(f"{customer_id}|{month}")
    _save_excluded_invoices(keys)
    return {"excluded": True, "key": f"{customer_id}|{month}"}


@router.delete("/invoices/exclude/{customer_id}")
async def restore_invoice(
    customer_id: str,
    month: str = Query(..., description="Billing month as YYYY-MM"),
):
    """Remove an invoice exclusion, restoring it to the invoice list."""
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    keys = _load_excluded_invoices()
    key  = f"{customer_id}|{month}"
    keys.discard(key)
    _save_excluded_invoices(keys)
    return {"excluded": False, "key": key}


@router.get("/invoices/excluded")
async def list_excluded_invoices():
    """Return the full list of excluded invoice keys."""
    return {"keys": sorted(_load_excluded_invoices())}


# --------------------------------------------------------------------------- #
#  SKU line-item override endpoints                                             #
#  POST   /invoices/sku-override           → set serial override               #
#  DELETE /invoices/sku-override           → clear serial override             #
#  GET    /invoices/sku-overrides          → list all overrides                #
# --------------------------------------------------------------------------- #

@router.post("/invoices/sku-override")
async def set_sku_override(body: _SkuOverrideBody, customer_id: str = Query(...)):
    """
    Override the resolved SKU for a specific device serial on a given invoice.
    Body: { serial: "SERIALNUM", skuKey: "...", month: "YYYY-MM" }
    """
    try:
        datetime.strptime(body.month, "%Y-%m")
    except ValueError:
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    serial_upper = body.serial.strip().upper()
    key          = f"{customer_id}|{body.month}|{serial_upper}"
    overrides    = _load_sku_overrides()
    overrides[key] = body.skuKey
    _save_sku_overrides(overrides)
    return {"key": key, "skuKey": body.skuKey}


@router.delete("/invoices/sku-override")
async def clear_sku_override(
    customer_id: str = Query(...),
    serial:      str = Query(...),
    month:       str = Query(..., description="YYYY-MM"),
):
    """Remove a per-serial SKU override, reverting to auto-resolution."""
    serial_upper = serial.strip().upper()
    key          = f"{customer_id}|{month}|{serial_upper}"
    overrides    = _load_sku_overrides()
    overrides.pop(key, None)
    _save_sku_overrides(overrides)
    return {"key": key, "cleared": True}


@router.get("/invoices/sku-overrides")
async def list_sku_overrides():
    """Return all active SKU overrides."""
    return {"overrides": _load_sku_overrides()}


@router.get("/invoices/sku-catalog")
async def get_sku_catalog():
    """
    Return the full SKU catalog (skuKey + fullPath + defaultPrice + category).
    Used by the frontend SKU override picker.
    """
    catalog = _load_json(os.path.join(_HERE, "sku_catalog.json"), [])
    return {
        "items": [
            {
                "skuKey":        s["skuKey"],
                "fullPath":      s.get("fullPath") or s["skuKey"],
                "defaultPrice":  s.get("defaultPrice") or 0,
                "category":      s.get("category") or "",
                "desc":          s.get("desc") or s["skuKey"],
            }
            for s in catalog
        ]
    }


# --------------------------------------------------------------------------- #
#  Unbilled-check: active devices with no qualifying date for the given month  #
# --------------------------------------------------------------------------- #

@router.get("/invoices/unbilled-check")
async def get_unbilled_check(
    month: str = Query(
        default="",
        description="Billing month as YYYY-MM (defaults to current month)",
    ),
):
    """
    Pre-flight scan: returns all active, non-terminated devices belonging to
    ELIGIBLE_BILLING_TYPES customers that have NO qualifying activation date
    for the given month.

    A device is "unbilled" for this month if it satisfies ANY of:
      - Neither firstDeviceActivationDate nor billingStartDate is set (Never Activated)
      - billingStartDate < firstDeviceActivationDate (auto-activated; skipped by engine)
      - The resolved activation date falls outside the billing month

    The override store (billing_date_overrides) is respected so that if the
    user has already set a date the device will NOT appear in this list.

    Response shape:
    {
      "billingMonth": "YYYY-MM",
      "count": N,
      "devices": [
        {
          "serial": "...",
          "companyId": "...",
          "companyName": "...",
          "billingType": "...",
          "reason": "never_activated" | "auto_activated" | "outside_month" | "no_date",
          "firstDeviceActivationDate": "YYYY-MM-DD" | "",
          "billingStartDate": "YYYY-MM-DD" | "",
          "hasOverride": bool
        }, ...
      ]
    }
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
        raise HTTPException(
            status_code=503,
            detail="No contract data cached. Please run a MyAdmin sync first.",
        )

    from .customers import billing_type_overrides, qb_customers

    month_start = date(b_year, b_month, 1)
    month_end   = date(b_year, b_month, _days_in_month(b_year, b_month))

    auto_assigned_count = 0  # track how many startDate auto-assigns happen this run

    # Group contracts by company (same logic as get_prorated_invoices)
    contracts_by_company: Dict[str, List[dict]] = defaultdict(list)
    for c in all_contracts:
        uc      = c.get("userContact") or {}
        company = uc.get("userCompany") or {}
        cid     = str(company.get("id") or "")
        if cid:
            contracts_by_company[cid].append(c)

    unbilled: List[dict] = []

    for company_id, company_contracts in contracts_by_company.items():
        if not company_contracts:
            continue

        # Resolve billing type (same priority chain as get_prorated_invoices)
        raw_name   = ((company_contracts[0].get("userContact") or {})
                      .get("userCompany") or {}).get("name") or ""
        clean_name = _clean_name(raw_name)
        qb_lookup_name = _strip_han_cs(_strip_sub_account_suffix(clean_name))
        is_han_cs  = clean_name.strip().lower().endswith("{han-cs}")
        bt = (
            billing_type_overrides.get(company_id)
            or (qb_customers.get(_normalize(qb_lookup_name)) or {}).get("billingType")
            or ("Han-CS" if is_han_cs else None)
            or "Unknown"
        )

        if bt not in ELIGIBLE_BILLING_TYPES:
            continue

        display_name = _strip_han_cs(clean_name)

        for c in company_contracts:
            if c.get("isTerminated"):
                continue

            dev    = c.get("device") or {}
            serial = (dev.get("serialNumber") or "").strip().upper()

            # Skip devices that have never been activated (no billing plan assigned).
            # These are undeployed units — the user only wants to see truly Active
            # devices that are missing a date.
            _adp_name = ((c.get("activeDevicePlan") or {}).get("name") or "").upper()
            _is_never_activated_plan = (
                not _adp_name
                or _adp_name == "NEVER ACTIVATED"
                or "NEVER" in _adp_name
            )
            if _is_never_activated_plan:
                continue

            # Check override
            override_bsd = billing_date_overrides.get(serial)
            has_override = override_bsd is not None

            raw_fcd = _safe_date(c.get("firstDeviceActivationDate"))
            raw_bsd = override_bsd or _safe_date(c.get("billingStartDate"))

            # Auto-assign startDate ("Assignment Date" in MyAdmin UI) when a device
            # has no First Connect and no Billing Start date.  Only apply if the
            # startDate is a valid ISO date that falls before the end of the
            # billing month (i.e. the device was assigned before this month closed).
            raw_sd = _safe_date(c.get("startDate"))
            if not raw_fcd and not raw_bsd and not override_bsd and raw_sd:
                try:
                    sd = date.fromisoformat(raw_sd)
                    if sd <= month_end:
                        billing_date_overrides[serial] = raw_sd
                        auto_assigned_count += 1
                        has_override = True
                        raw_bsd = raw_sd
                except ValueError:
                    pass  # unparseable startDate — fall through to no_date logic

            # Determine reason this device has no qualifying date
            reason: Optional[str] = None

            if not raw_fcd and not raw_bsd:
                reason = "never_activated"
            elif raw_fcd and raw_bsd:
                try:
                    fcd = date.fromisoformat(raw_fcd)
                    bsd = date.fromisoformat(raw_bsd)
                    if bsd < fcd:
                        reason = "auto_activated"
                    else:
                        # Rule 3: use fcd
                        if not (month_start <= fcd <= month_end):
                            reason = "outside_month"
                except ValueError:
                    reason = "no_date"
            elif raw_fcd:
                try:
                    fcd = date.fromisoformat(raw_fcd)
                    if not (month_start <= fcd <= month_end):
                        reason = "outside_month"
                except ValueError:
                    reason = "no_date"
            else:
                # Rule 4: only bsd
                try:
                    bsd = date.fromisoformat(raw_bsd)
                    if not (month_start <= bsd <= month_end):
                        reason = "outside_month"
                except ValueError:
                    reason = "no_date"

            if reason in ("never_activated", "no_date"):
                unbilled.append({
                    "serial":                    serial or dev.get("serialNumber") or "",
                    "companyId":                 company_id,
                    "companyName":               display_name,
                    "billingType":               bt,
                    "reason":                    reason,
                    "firstDeviceActivationDate": raw_fcd,
                    "billingStartDate":          raw_bsd,
                    "hasOverride":               has_override,
                })

    # Persist any startDate auto-assigns in a single write (avoids per-device I/O)
    if auto_assigned_count:
        _save_json(BILLING_DATE_OVERRIDES_FILE, billing_date_overrides)

    # Sort by company name then serial
    unbilled.sort(key=lambda x: (x["companyName"].lower(), x["serial"]))

    return {
        "billingMonth":      f"{b_year}-{b_month:02d}",
        "count":             len(unbilled),
        "autoAssigned":      auto_assigned_count,
        "devices":           unbilled,
    }
