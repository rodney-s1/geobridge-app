"""
reconciliation.py -- Billing Reconciliation API
===============================================
Compares what MyAdmin says customers have (devices + rate plan codes)
against the QB SKU catalog and per-customer price overrides to surface
billing discrepancies.

Key logic:
  1. Load all cached MyAdmin contracts.  Each contract has two relevant fields:
       promoCode          -- optional promo/reseller code (e.g. "SWELL", "BUNDLE-GO")
                            Most devices do NOT have one.
       activeDevicePlan   -- the actual billing tier every device has
                            (e.g. "ProPlus Mode", "Base Mode: Live", "GO9 Focus Plus")
  2. For each device, resolve a skuKey via a 4-tier lookup:
       Tier 1: customer-specific mapping on promoCode  (highest priority)
       Tier 2: customer-specific mapping on billingPlan name
       Tier 3: global mapping on promoCode
       Tier 4: global mapping on billingPlan name
  3. Look up the expected price:
       - customer-specific override (from sku_customer_overrides) if it exists
       - else catalog default price
  4. Look up the actual QB-invoiced price from sku_customer_overrides
     (since that's where invoice import stored per-customer prices).
  5. Emit per-device rows and per-customer summary rows with status:
       ok             -- expected == actual
       over           -- actual > expected  (customer billed more than catalog)
       under          -- actual < expected  (customer billed less than catalog)
       unmapped       -- no SKU mapping found for promoCode or billingPlan
       no_price       -- SKU exists but no price anywhere
       not_in_qb      -- customer has no QB data / no invoiced price
       never_activated -- device has never been activated in MyAdmin

NEVER ACTIVATED device billing rules:
  - Standard customers: bill the device at the same SKU/price as other active
    devices on that account (they are on the hook for it regardless).
    The device appears in reconciliation with status="never_activated" and
    inherits the account's most common active rate plan SKU.
  - CUA (Charge Upon Activation) customers: NEVER ACTIVATED devices are
    excluded from reconciliation entirely — they are not billed until active.

NOTE: sku_mappings.json must contain entries for BOTH promo codes (e.g.
"SWELL") AND billing plan names (e.g. "PROPLUS MODE") to get full coverage.
Billing plan names should be stored uppercase in ratePlanCode.
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Optional, Tuple
import json
import os

router = APIRouter()

_HERE = os.path.dirname(os.path.abspath(__file__))

# --- Shared stores (imported lazily so circular-import safe) -----------------
def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _get_stores():
    """Load fresh copies from disk each call so restarts aren't needed."""
    catalog      = _load(os.path.join(_HERE, "sku_catalog.json"),                    [])
    mappings     = _load(os.path.join(_HERE, "sku_mappings.json"),                   [])
    cust_maps    = _load(os.path.join(_HERE, "customer_rate_plan_mappings.json"),     [])
    overrides    = _load(os.path.join(_HERE, "sku_customer_overrides.json"),          [])
    qb_qtys      = _load(os.path.join(_HERE, "qb_invoice_quantities.json"),           [])
    return catalog, mappings, cust_maps, overrides, qb_qtys


def _normalize(s: str) -> str:
    """Normalise a customer name for index lookups.

    Strips MyAdmin sub-account suffixes enclosed in braces so that
    sub-accounts like 'Acme Corp {3rd Party Devices}' match against the
    QuickBooks invoice name 'Acme Corp' — they are always on the same
    invoice, just different line items.
    """
    s = (s or "").strip()
    # Strip trailing {...} sub-account qualifier (e.g. "{3rd Party Devices}")
    brace = s.find("{")
    if brace != -1:
        s = s[:brace].strip()
    return s.lower()


# --- Helper: resolve expected price for (customerName, skuKey) ---------------
def _resolve_price(customer_name: str, sku_key: str,
                   ovr_index: dict, catalog_index: dict) -> Tuple[Optional[float], str]:
    """
    Returns (price, source) where source is 'override' | 'catalog' | 'none'.
    ovr_index  : {(norm_customer, skuKey) -> price}
    catalog_index: {skuKey -> defaultPrice}
    """
    key = (_normalize(customer_name), sku_key)
    if key in ovr_index:
        return ovr_index[key], "override"
    cat = catalog_index.get(sku_key)
    if cat is not None and cat > 0:
        return cat, "catalog"
    return None, "none"


# ===============================================================================
#  GET /api/reconciliation
# ===============================================================================

@router.get("/reconciliation")
async def get_reconciliation(customer_id: str = "", status_filter: str = ""):
    """
    Returns reconciliation data for all customers (or one if customer_id given).

    Response shape:
    {
      "summary": {
        "totalDevices": int,
        "totalCustomers": int,
        "ok": int,
        "over": int,
        "under": int,
        "unmapped": int,
        "noPrice": int,
        "notInQb": int,
        "monthlyExpected": float,
        "monthlyActual": float,
        "monthlyDelta": float
      },
      "customers": [
        {
          "customerId": str,
          "customerName": str,
          "deviceCount": int,
          "ok": int, "over": int, "under": int,
          "unmapped": int, "noPrice": int,
          "expectedMonthly": float,
          "actualMonthly": float,
          "delta": float,
          "status": "ok" | "discrepancy" | "unmapped" | "no_price",
          "devices": [
            {
              "serialNumber": str,
              "ratePlanCode": str,
              "skuKey": str,
              "skuName": str,
              "expectedPrice": float | null,
              "actualPrice": float | null,
              "delta": float | null,
              "priceSource": "override" | "catalog" | "none",
              "status": "ok"|"over"|"under"|"unmapped"|"no_price"|"not_in_qb"
            }
          ]
        }
      ]
    }
    """
    # -- Import here to avoid circular imports ---------------------------------
    from geotab.customers import _sync_cache, enrich_customer

    contracts = _sync_cache.get("contracts") or []
    if not contracts:
        raise HTTPException(
            status_code=503,
            detail="No MyAdmin contract data cached yet. "
                   "Please open the Customers page to trigger a sync first."
        )

    device_db_records = _sync_cache.get("device_db_records") or []

    # Build a billing-type index: companyId -> billingType ("CUA", "Standard", ...)
    # Used to determine whether NEVER ACTIVATED devices should be billed.
    raw_customers = _sync_cache.get("raw_customers") or []
    billing_type_index: Dict[str, str] = {}
    for raw_c in raw_customers:
        enriched = enrich_customer(raw_c)
        cid_raw  = str(raw_c.get("companyId") or enriched.get("id") or "")
        if cid_raw:
            billing_type_index[cid_raw] = enriched.get("billingType") or "Standard"

    # -- Build indexes ---------------------------------------------------------
    catalog, mappings, cust_maps, overrides, qb_qtys = _get_stores()

    # skuKey -> defaultPrice
    catalog_index: Dict[str, float] = {
        s["skuKey"]: float(s.get("defaultPrice") or 0)
        for s in catalog
    }
    # skuKey -> skuName (full label)
    catalog_name: Dict[str, str] = {
        s["skuKey"]: s.get("skuKey", "")
        for s in catalog
    }

    # Tier 1: (norm_customerName, ratePlanCode_upper) -> skuKey
    cust_mapping_index: Dict[tuple, str] = {
        (_normalize(m["customerName"]), (m.get("ratePlanCode") or "").upper()): m.get("skuKey") or ""
        for m in cust_maps
    }

    # Tier 2: ratePlanCode (upper) -> skuKey  (global default)
    mapping_index: Dict[str, str] = {
        (m.get("ratePlanCode") or m.get("promoCode") or "").upper(): m.get("skuKey") or ""
        for m in mappings
    }

    # (norm_customerName, skuKey) -> price
    ovr_index: Dict[tuple, float] = {
        (_normalize(o["customerName"]), o["skuKey"]): float(o.get("price") or 0)
        for o in overrides
    }

    # QB invoice quantities: (norm_customerName, skuKey) -> qbQty
    qb_qty_index: Dict[tuple, int] = {
        (_normalize(q["customerName"]), q["skuKey"]): int(q.get("qbQty") or 0)
        for q in qb_qtys
    }

    # -- Group contracts by company --------------------------------------------
    device_db_map: Dict[str, str] = {}
    for rec in device_db_records:
        dev_id  = str(rec.get("DeviceId") or rec.get("deviceId") or "")
        db_name = rec.get("DatabaseName") or rec.get("databaseName") or ""
        if dev_id:
            device_db_map[dev_id] = db_name

    # parent_key (normalised parent name) -> {name, billingType, devices: [...]}
    # Sub-accounts like "Hoopaugh Grading LLC {3rd Party Devices}" are merged
    # under their parent "Hoopaugh Grading LLC" so QB quantities are not repeated.
    company_map: Dict[str, dict] = {}

    # First pass: build a set of all sub-account cids that belong to each parent
    # so the customer_id filter (which is a cid) can match any sub-account.
    _parent_key_for_cid: Dict[str, str] = {}  # cid -> parent_key

    for c in contracts:
        if c.get("isTerminated"):
            continue
        uc      = c.get("userContact") or {}
        company = uc.get("userCompany") or {}
        cid     = str(company.get("id") or "")
        cname   = company.get("name") or ""
        if not cid:
            continue

        # Strip {sub-account} suffix to get the parent name and its key.
        brace = cname.find("{")
        parent_name = cname[:brace].strip() if brace != -1 else cname
        parent_key  = _normalize(parent_name)
        _parent_key_for_cid[cid] = parent_key

    # If filtering by customer_id, find the parent_key that owns that cid.
    _filter_parent_key: str = ""
    if customer_id:
        _filter_parent_key = _parent_key_for_cid.get(customer_id, "")

    for c in contracts:
        if c.get("isTerminated"):
            continue
        uc      = c.get("userContact") or {}
        company = uc.get("userCompany") or {}
        cid     = str(company.get("id") or "")
        cname   = company.get("name") or ""
        if not cid:
            continue

        # Derive parent name / key (same logic as first pass).
        brace = cname.find("{")
        parent_name = cname[:brace].strip() if brace != -1 else cname
        parent_key  = _normalize(parent_name)

        # Apply customer_id filter: include only contracts whose parent group
        # contains the requested cid.
        if customer_id and parent_key != _filter_parent_key:
            continue

        device = c.get("device") or {}
        dev_id = str(device.get("id") or "")
        serial = device.get("serialNumber") or dev_id

        # promoCode is an optional reseller/promo override (e.g. "SWELL", "BUNDLE-GO").
        # Most devices don't have one.  activeDevicePlan.name is the actual billing
        # tier every device has (e.g. "ProPlus Mode", "Base Mode: Live").
        promo_code   = (c.get("promoCode") or "").upper().strip()
        adp          = c.get("activeDevicePlan") or {}
        billing_plan = (adp.get("name") or "").strip()

        # MyAdmin appends status suffixes to plan names: "Base Mode: Live",
        # "Pro Mode: Live", "Regulatory Mode: Live", etc.
        # Strip everything from the first colon onward so "Base Mode: Live"
        # normalises to "Base Mode" and matches the sku_mappings entry.
        if ":" in billing_plan:
            billing_plan = billing_plan.split(":")[0].strip()

        # Detect never-activated devices: MyAdmin sets activeDevicePlan.name to
        # "NEVER ACTIVATED" (or leaves it blank) and billingStatus = "Never billed".
        adp_upper        = billing_plan.upper()
        is_never_activated = (
            adp_upper == "NEVER ACTIVATED"
            or adp_upper == ""
            or "never" in adp_upper
        )

        if parent_key not in company_map:
            # Use the first cid seen for billing-type lookup (parent account
            # typically has the lowest/canonical cid among its sub-accounts).
            cust_billing_type = billing_type_index.get(cid, "Standard")
            company_map[parent_key] = {
                "customerId":       cid,
                "customerName":     parent_name,
                "billingType":      cust_billing_type,
                "devices":          [],
                "subAccountIds":    set(),
                "subAccountNames":  set(),
            }
        # Always record the sub-account cid and display name.
        company_map[parent_key]["subAccountIds"].add(cid)
        if cname != parent_name:
            company_map[parent_key]["subAccountNames"].add(cname)
        # Prefer the parent name (no braces) as the display name.
        if not company_map[parent_key]["customerName"] and parent_name:
            company_map[parent_key]["customerName"] = parent_name

        company_map[parent_key]["devices"].append({
            "serialNumber":    serial,
            "promoCode":       promo_code,
            "billingPlan":     billing_plan,
            "neverActivated":  is_never_activated,
        })

    # -- Per-device reconciliation ---------------------------------------------
    STATUS_PRIORITY = {"discrepancy": 0, "unmapped": 1, "no_price": 2, "not_in_qb": 3, "ok": 4}

    result_customers = []

    total_ok = total_over = total_under = total_unmapped = 0
    total_no_price = total_not_in_qb = 0
    total_expected = total_actual = 0.0
    total_myadmin_devices = 0
    total_qb_devices = 0
    total_qty_match = total_qty_over = total_qty_under = total_qty_missing = 0

    for _pkey, cdata in company_map.items():
        cid          = cdata["customerId"]
        cname        = cdata["customerName"]
        devices      = cdata["devices"]
        # Re-scan all sub-account cids so CUA billing type is found even when
        # the first cid seen belongs to a sub-account rather than the parent.
        sub_cids     = cdata.get("subAccountIds") or {cid}
        billing_type = next(
            (billing_type_index[c] for c in sub_cids if c in billing_type_index),
            cdata.get("billingType") or "Standard",
        )
        is_cua       = billing_type in ("CUA", "Charge Upon Activation")

        cust_ok = cust_over = cust_under = cust_unmapped = cust_no_price = 0
        cust_never_activated = 0
        cust_expected = cust_actual = 0.0
        device_rows = []

        # Separate active from never-activated devices so we can inherit SKUs
        active_devices       = [d for d in devices if not d.get("neverActivated")]
        never_activated_devs = [d for d in devices if d.get("neverActivated")]

        # CUA customers: never-activated devices are not billed — skip entirely.
        # Standard customers: process active devices first, then inherit SKU for
        # never-activated devices from the most common active SKU on the account.
        devices_to_process = active_devices if is_cua else devices

        # Track SKU usage for active devices so never-activated can inherit
        active_sku_counts: Dict[str, int] = {}

        for dev in devices_to_process:
            promo_code      = dev["promoCode"]       # e.g. "SWELL", "" (most devices)
            billing_plan    = dev["billingPlan"]     # e.g. "ProPlus Mode", "Base Mode" (suffix already stripped)
            norm_cname      = _normalize(cname)
            never_activated = dev.get("neverActivated", False)

            # CUA: already filtered above.
            # Standard: never-activated devices inherit the most common active SKU —
            # handled after the active-devices loop below (skip normal lookup for them).
            if never_activated:
                # Defer to post-loop processing
                continue

            # --- 4-tier SKU lookup -------------------------------------------
            # Tier 1: customer-specific mapping on promoCode (highest priority)
            sku_key      = None
            lookup_code  = ""   # what we actually matched on (for display)
            mapping_tier = "none"

            if promo_code:
                sku_key = cust_mapping_index.get((norm_cname, promo_code), None)
                if sku_key is not None:
                    mapping_tier = "customer"
                    lookup_code  = promo_code

            # Tier 2: customer-specific mapping on billingPlan name
            if sku_key is None and billing_plan:
                bp_upper = billing_plan.upper()
                sku_key  = cust_mapping_index.get((norm_cname, bp_upper), None)
                if sku_key is not None:
                    mapping_tier = "customer"
                    lookup_code  = billing_plan

            # Tier 3: global mapping on promoCode
            if sku_key is None and promo_code:
                sku_key = mapping_index.get(promo_code, None)
                if sku_key is not None:
                    mapping_tier = "global"
                    lookup_code  = promo_code

            # Tier 4: global mapping on billingPlan name
            if sku_key is None and billing_plan:
                bp_upper = billing_plan.upper()
                sku_key  = mapping_index.get(bp_upper, None)
                if sku_key is not None:
                    mapping_tier = "global"
                    lookup_code  = billing_plan

            if sku_key is None:
                sku_key      = ""
                mapping_tier = "none"

            # -- DM Service Fee family normalisation (Tier 4.5) ---------------
            # CELU-TP-250 / CELU-TP-200 promo codes map globally to the bare
            # "DM Service Fee" skuKey, but QB invoices customers at a specific
            # variant: DM Service Fee (Periodic), DM Service Fee (Enterprise),
            # DM Service Fee (Movement), etc.
            #
            # Rule: if the resolved skuKey is "DM Service Fee" (bare), scan the
            # customer's override index for any key that STARTS WITH "DM Service
            # Fee" and upgrade to that specific variant.  If the customer has
            # multiple DM Service Fee variants, pick the one with the most QB
            # invoice quantity (most likely match for this device).
            if sku_key == "DM Service Fee":
                dm_variants = [
                    sk for (nc, sk) in ovr_index.keys()
                    if nc == norm_cname and sk.startswith("DM Service Fee") and sk != "DM Service Fee"
                ]
                if dm_variants:
                    # Prefer the variant with the highest QB invoice quantity;
                    # fall back to override price ordering if no qty data.
                    dm_variants.sort(
                        key=lambda sk: qb_qty_index.get((norm_cname, sk), 0),
                        reverse=True,
                    )
                    sku_key = dm_variants[0]
            # -----------------------------------------------------------------

            # Track active SKU usage for never-activated inheritance
            if sku_key:
                active_sku_counts[sku_key] = active_sku_counts.get(sku_key, 0) + 1

            # Display label: prefer promoCode if it exists, else billingPlan
            display_plan = promo_code or billing_plan or "(none)"

            if mapping_tier == "none":
                # No mapping found on either promoCode or billingPlan
                device_rows.append({
                    "serialNumber":  dev["serialNumber"],
                    "ratePlanCode":  display_plan,
                    "skuKey":        "",
                    "skuName":       "",
                    "expectedPrice": None,
                    "actualPrice":   None,
                    "delta":         None,
                    "priceSource":   "none",
                    "status":        "unmapped",
                })
                cust_unmapped += 1
                continue

            if not sku_key:
                # Mapping key matched but skuKey is blank
                device_rows.append({
                    "serialNumber":  dev["serialNumber"],
                    "ratePlanCode":  display_plan,
                    "skuKey":        "",
                    "skuName":       "",
                    "expectedPrice": None,
                    "actualPrice":   None,
                    "delta":         None,
                    "priceSource":   "none",
                    "status":        "unmapped",
                })
                cust_unmapped += 1
                continue

            rate_plan = lookup_code  # used by the rest of the loop for display

            # Resolve expected price
            expected, price_source = _resolve_price(cname, sku_key, ovr_index, catalog_index)

            if expected is None:
                device_rows.append({
                    "serialNumber": dev["serialNumber"],
                    "ratePlanCode": rate_plan,
                    "skuKey":       sku_key,
                    "skuName":      catalog_name.get(sku_key, sku_key),
                    "expectedPrice": None,
                    "actualPrice":   None,
                    "delta":         None,
                    "priceSource":   "none",
                    "status":        "no_price",
                })
                cust_no_price += 1
                continue

            # Resolve actual QB invoiced price (customer override is the invoice truth)
            actual_key = (_normalize(cname), sku_key)
            actual = ovr_index.get(actual_key)

            if actual is None:
                # Customer has no QB invoice price for this SKU
                device_rows.append({
                    "serialNumber": dev["serialNumber"],
                    "ratePlanCode": rate_plan,
                    "skuKey":       sku_key,
                    "skuName":      catalog_name.get(sku_key, sku_key),
                    "expectedPrice": round(expected, 2),
                    "actualPrice":   None,
                    "delta":         None,
                    "priceSource":   price_source,
                    "status":        "not_in_qb",
                })
                total_not_in_qb += 1
                cust_expected += expected
                continue

            delta = round(actual - expected, 2)
            if abs(delta) < 0.005:
                status = "ok"
                cust_ok += 1
            elif actual > expected:
                status = "over"
                cust_over += 1
            else:
                status = "under"
                cust_under += 1

            cust_expected += expected
            cust_actual   += actual

            device_rows.append({
                "serialNumber": dev["serialNumber"],
                "ratePlanCode": rate_plan,
                "skuKey":       sku_key,
                "skuName":      catalog_name.get(sku_key, sku_key),
                "expectedPrice": round(expected, 2),
                "actualPrice":   round(actual, 2),
                "delta":         delta,
                "priceSource":   price_source,
                "status":        status,
            })

        # -- Never-activated devices (Standard customers only) -----------------
        # CUA customers: already excluded from devices_to_process above.
        # Standard customers: never-activated devices are billed at the same SKU
        # as the other active devices on the account.  Inherit the most-used
        # active SKU.  If no active devices exist yet (brand-new account), we
        # emit them as "unmapped" so they show up visibly.
        if not is_cua and never_activated_devs:
            # Pick the most common active SKU (or "" if no active devices)
            inherited_sku = (
                max(active_sku_counts, key=active_sku_counts.get)
                if active_sku_counts else ""
            )
            for na_dev in never_activated_devs:
                if inherited_sku:
                    expected_na, price_source_na = _resolve_price(
                        cname, inherited_sku, ovr_index, catalog_index
                    )
                    device_rows.append({
                        "serialNumber":  na_dev["serialNumber"],
                        "ratePlanCode":  "Never Activated",
                        "skuKey":        inherited_sku,
                        "skuName":       catalog_name.get(inherited_sku, inherited_sku),
                        "expectedPrice": round(expected_na, 2) if expected_na is not None else None,
                        "actualPrice":   None,   # QB won't have a line for this device specifically
                        "delta":         None,
                        "priceSource":   price_source_na,
                        "status":        "never_activated",
                        "neverActivated": True,
                    })
                    cust_never_activated += 1
                    # Count toward MyAdmin SKU total — they should appear in quantity
                    if expected_na is not None:
                        cust_expected += expected_na
                else:
                    # No active devices to inherit SKU from — show as unmapped
                    device_rows.append({
                        "serialNumber":  na_dev["serialNumber"],
                        "ratePlanCode":  "Never Activated",
                        "skuKey":        "",
                        "skuName":       "",
                        "expectedPrice": None,
                        "actualPrice":   None,
                        "delta":         None,
                        "priceSource":   "none",
                        "status":        "never_activated",
                        "neverActivated": True,
                    })
                    cust_never_activated += 1

        # -- Customer-level status ---------------------------------------------
        has_discrepancy    = (cust_over + cust_under) > 0
        has_unmapped       = cust_unmapped > 0
        has_no_price       = cust_no_price > 0
        has_never_activated = cust_never_activated > 0

        if has_discrepancy:
            cust_status = "discrepancy"
        elif has_unmapped:
            cust_status = "unmapped"
        elif has_no_price:
            cust_status = "no_price"
        else:
            cust_status = "ok"

        # -- Sort device rows by rate plan so same-plan devices are grouped ------
        # Sort key: (ratePlanCode lower, serialNumber) so plans group together
        # and within each group serials are alphabetical.
        # Never-activated devices sort last within their inherited rate plan.
        device_rows.sort(key=lambda r: (
            r.get("ratePlanCode") or "zzz",   # blank/none sorts last
            0 if not r.get("neverActivated") else 1,
            r.get("serialNumber") or "",
        ))

        # -- Quantity reconciliation per SKU for this customer -----------------
        # Count MyAdmin devices per skuKey (mapped + never_activated devices).
        # Never-activated devices on Standard accounts count toward the SKU qty
        # because the customer is billed for them regardless of activation status.
        myadmin_by_sku: Dict[str, int] = {}
        for row in device_rows:
            sk = row.get("skuKey") or ""
            if sk:
                myadmin_by_sku[sk] = myadmin_by_sku.get(sk, 0) + 1

        norm_cname = _normalize(cname)
        qty_rows = []
        cust_qty_match = cust_qty_over = cust_qty_under = cust_qty_missing = 0

        # All SKUs seen either in MyAdmin mapping or in QB invoice for this customer
        all_skus = set(myadmin_by_sku.keys()) | {
            sk for (nc, sk) in qb_qty_index.keys() if nc == norm_cname
        }

        # Count unmapped devices (no rate plan OR no mapping, excl. never_activated)
        cust_unmapped_count = sum(
            1 for row in device_rows
            if row.get("status") == "unmapped" and not row.get("neverActivated")
        )

        for sku_key in sorted(all_skus):
            myadmin_count = myadmin_by_sku.get(sku_key, 0)
            qb_qty        = qb_qty_index.get((norm_cname, sku_key), None)
            qty_delta     = (myadmin_count - qb_qty) if qb_qty is not None else None

            if qb_qty is None:
                qty_status = "no_qb_data"
                cust_qty_missing += 1
            elif qty_delta == 0:
                qty_status = "match"
                cust_qty_match += 1
            elif myadmin_count > qb_qty:
                qty_status = "under_billed"   # MyAdmin has MORE devices than QB billed
                cust_qty_under += 1
            else:
                qty_status = "over_billed"    # QB billed MORE than MyAdmin devices
                cust_qty_over += 1

            qty_rows.append({
                "skuKey":              sku_key,
                "myAdminCount":        myadmin_count,
                "qbQty":               qb_qty,
                "qtyDelta":            qty_delta,
                "qtyStatus":           qty_status,
                "unmappedCount":       cust_unmapped_count,
                "neverActivatedCount": cust_never_activated,
            })

        cust_myadmin_total = len(devices)
        cust_qb_total      = sum(r["qbQty"] for r in qty_rows if r["qbQty"] is not None)
        has_qb_data        = any(r["qbQty"] is not None for r in qty_rows)

        # -- Apply status filter -----------------------------------------------
        if status_filter and cust_status != status_filter:
            continue

        total_ok        += cust_ok
        total_over      += cust_over
        total_under     += cust_under
        total_unmapped  += cust_unmapped
        total_no_price  += cust_no_price
        total_expected  += cust_expected
        total_actual    += cust_actual
        total_myadmin_devices += cust_myadmin_total
        total_qb_devices      += cust_qb_total
        total_qty_match       += cust_qty_match
        total_qty_over        += cust_qty_over
        total_qty_under       += cust_qty_under
        total_qty_missing     += cust_qty_missing

        result_customers.append({
            "customerId":        cid,
            "customerName":      cname,
            "billingType":       billing_type,
            "subAccountNames":   sorted(cdata.get("subAccountNames") or []),
            "deviceCount":       len(devices),
            "ok":                cust_ok,
            "over":              cust_over,
            "under":             cust_under,
            "unmapped":          cust_unmapped,
            "neverActivated":    cust_never_activated,
            "noPrice":           cust_no_price,
            "expectedMonthly":   round(cust_expected, 2),
            "actualMonthly":     round(cust_actual, 2),
            "delta":             round(cust_actual - cust_expected, 2),
            "status":            cust_status,
            "devices":           device_rows,
            # Quantity reconciliation fields
            "myAdminTotal":      cust_myadmin_total,
            "qbTotal":           cust_qb_total if has_qb_data else None,
            "qtyDelta":          (cust_myadmin_total - cust_qb_total) if has_qb_data else None,
            "qtyMatch":          cust_qty_match,
            "qtyUnderBilled":    cust_qty_under,
            "qtyOverBilled":     cust_qty_over,
            "qtyMissing":        cust_qty_missing,
            "hasQbData":         has_qb_data,
            "skuQtyBreakdown":   qty_rows,
        })

    # Sort: discrepancy first, then unmapped, then no_price, then ok; alpha within
    result_customers.sort(key=lambda c: (
        STATUS_PRIORITY.get(c["status"], 99),
        (c["customerName"] or "").lower()
    ))

    total_devices = sum(c["deviceCount"] for c in result_customers)
    monthly_delta = round(total_actual - total_expected, 2)

    return {
        "summary": {
            "totalDevices":    total_devices,
            "totalCustomers":  len(result_customers),
            "ok":              total_ok,
            "over":            total_over,
            "under":           total_under,
            "unmapped":        total_unmapped,
            "noPrice":         total_no_price,
            "notInQb":         total_not_in_qb,
            "monthlyExpected": round(total_expected, 2),
            "monthlyActual":   round(total_actual, 2),
            "monthlyDelta":    monthly_delta,
            # Quantity reconciliation totals
            "myAdminTotal":    total_myadmin_devices,
            "qbTotal":         total_qb_devices,
            "qtyDelta":        total_myadmin_devices - total_qb_devices,
            "qtyMatch":        total_qty_match,
            "qtyUnderBilled":  total_qty_under,
            "qtyOverBilled":   total_qty_over,
            "qtyMissing":      total_qty_missing,
            "hasQbData":       total_qb_devices > 0,
        },
        "customers": result_customers,
    }


# ===============================================================================
#  GET /api/reconciliation/billing-plans
#  Returns all unique activeDevicePlan names from the MyAdmin cache with
#  device counts and whether they are currently mapped in sku_mappings.
# ===============================================================================

@router.get("/reconciliation/billing-plans")
async def get_billing_plans():
    """
    Returns all unique billing plan names (activeDevicePlan.name) seen across
    all cached MyAdmin contracts, with device counts and mapping status.
    Use this to identify which plan names need entries in Rate Plan Mappings.
    """
    from geotab.customers import _sync_cache

    contracts = _sync_cache.get("contracts") or []
    _, mappings, _, _, _ = _get_stores()

    # Build mapping index (upper) -> skuKey
    mapping_index = {
        (m.get("ratePlanCode") or m.get("promoCode") or "").upper(): m.get("skuKey") or ""
        for m in mappings
    }

    plan_counts: dict = {}
    for c in contracts:
        if c.get("isTerminated"):
            continue
        adp  = c.get("activeDevicePlan") or {}
        name = (adp.get("name") or "").strip()
        # Strip MyAdmin status suffix (e.g. "Base Mode: Live" -> "Base Mode")
        if ":" in name:
            name = name.split(":")[0].strip()
        if name:
            plan_counts[name] = plan_counts.get(name, 0) + 1

    result = []
    for name, count in sorted(plan_counts.items(), key=lambda x: -x[1]):
        sku_key = mapping_index.get(name.upper(), None)
        result.append({
            "billingPlan":  name,
            "deviceCount":  count,
            "mapped":       sku_key is not None and sku_key != "",
            "skuKey":       sku_key or "",
        })

    return {
        "totalPlans":    len(result),
        "unmappedPlans": sum(1 for r in result if not r["mapped"]),
        "plans":         result,
    }


# ===============================================================================
#  GET /api/reconciliation/customer/{customer_id}
# ===============================================================================

@router.get("/reconciliation/customer/{customer_id}")
async def get_customer_reconciliation(customer_id: str):
    """Single-customer reconciliation with full device list."""
    data = await get_reconciliation(customer_id=customer_id)
    customers = data.get("customers") or []
    if not customers:
        raise HTTPException(status_code=404, detail="Customer not found or no devices")
    return customers[0]


# ===============================================================================
#  GET /api/reconciliation/debug-contracts
#  Dumps raw promoCode + billingPlan for every contract matching a customer name
# ===============================================================================

@router.get("/reconciliation/debug-contracts")
async def debug_contracts(customer_name: str = ""):
    """Debug: show raw promoCode and activeDevicePlan.name for contracts."""
    from geotab.customers import _sync_cache
    contracts = _sync_cache.get("contracts") or []

    seen_combos: dict = {}  # (promo, plan, company) -> count

    for c in contracts:
        if c.get("isTerminated"):
            continue
        uc      = c.get("userContact") or {}
        company = (uc.get("userCompany") or {}).get("name") or ""
        if customer_name and customer_name.lower() not in company.lower():
            continue
        promo = (c.get("promoCode") or "").strip()
        adp   = c.get("activeDevicePlan") or {}
        plan  = (adp.get("name") or "").strip()
        key   = (promo, plan, company)
        seen_combos[key] = seen_combos.get(key, 0) + 1

    results = []
    for (promo, plan, company), count in sorted(seen_combos.items(), key=lambda x: -x[1]):
        results.append({
            "customerName": company,
            "promoCode":    promo or "(none)",
            "billingPlan":  plan  or "(none)",
            "deviceCount":  count,
        })

    return {"totalGroups": len(results), "groups": results}
