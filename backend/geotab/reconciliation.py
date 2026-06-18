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
       ok         -- expected == actual
       over        -- actual > expected  (customer billed more than catalog)
       under       -- actual < expected  (customer billed less than catalog)
       unmapped    -- no SKU mapping found for promoCode or billingPlan
       no_price    -- SKU exists but no price anywhere
       not_in_qb   -- customer has no QB data / no invoiced price

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
    return (s or "").strip().lower()


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
    from geotab.customers import _sync_cache

    contracts = _sync_cache.get("contracts") or []
    if not contracts:
        raise HTTPException(
            status_code=503,
            detail="No MyAdmin contract data cached yet. "
                   "Please open the Customers page to trigger a sync first."
        )

    device_db_records = _sync_cache.get("device_db_records") or []

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

    # company_id -> {name, devices: [...]}
    company_map: Dict[str, dict] = {}

    for c in contracts:
        if c.get("isTerminated"):
            continue
        uc      = c.get("userContact") or {}
        company = uc.get("userCompany") or {}
        cid     = str(company.get("id") or "")
        cname   = company.get("name") or ""
        if not cid:
            continue
        if customer_id and cid != customer_id:
            continue

        device = c.get("device") or {}
        dev_id = str(device.get("id") or "")
        serial = device.get("serialNumber") or dev_id

        # promoCode is an optional reseller/promo override (e.g. "SWELL", "BUNDLE-GO").
        # Most devices don't have one.  activeDevicePlan.name is the actual billing
        # tier every device has (e.g. "ProPlus Mode", "Base Mode: Live").
        promo_code  = (c.get("promoCode") or "").upper().strip()
        adp         = c.get("activeDevicePlan") or {}
        billing_plan = (adp.get("name") or "").strip()

        if cid not in company_map:
            company_map[cid] = {"customerId": cid, "customerName": cname, "devices": []}
        if not company_map[cid]["customerName"] and cname:
            company_map[cid]["customerName"] = cname

        company_map[cid]["devices"].append({
            "serialNumber":  serial,
            "promoCode":     promo_code,
            "billingPlan":   billing_plan,
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

    for cid, cdata in company_map.items():
        cname   = cdata["customerName"]
        devices = cdata["devices"]

        cust_ok = cust_over = cust_under = cust_unmapped = cust_no_price = 0
        cust_expected = cust_actual = 0.0
        device_rows = []

        for dev in devices:
            promo_code   = dev["promoCode"]       # e.g. "SWELL", "" (most devices)
            billing_plan = dev["billingPlan"]     # e.g. "ProPlus Mode", "Base Mode: Live"
            norm_cname   = _normalize(cname)

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
            # -----------------------------------------------------------------

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

        # -- Customer-level status ---------------------------------------------
        has_discrepancy = (cust_over + cust_under) > 0
        has_unmapped    = cust_unmapped > 0
        has_no_price    = cust_no_price > 0

        if has_discrepancy:
            cust_status = "discrepancy"
        elif has_unmapped:
            cust_status = "unmapped"
        elif has_no_price:
            cust_status = "no_price"
        else:
            cust_status = "ok"

        # -- Quantity reconciliation per SKU for this customer -----------------
        # Count MyAdmin devices per skuKey (mapped devices only)
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

        # Count unmapped devices for this customer (no rate plan OR no mapping)
        cust_unmapped_count = sum(
            1 for row in device_rows if row.get("status") == "unmapped"
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
                "skuKey":         sku_key,
                "myAdminCount":   myadmin_count,
                "qbQty":          qb_qty,
                "qtyDelta":       qty_delta,
                "qtyStatus":      qty_status,
                "unmappedCount":  cust_unmapped_count,  # how many devices for this customer are unmapped
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
            "customerId":       cid,
            "customerName":     cname,
            "deviceCount":      len(devices),
            "ok":               cust_ok,
            "over":             cust_over,
            "under":            cust_under,
            "unmapped":         cust_unmapped,
            "noPrice":          cust_no_price,
            "expectedMonthly":  round(cust_expected, 2),
            "actualMonthly":    round(cust_actual, 2),
            "delta":            round(cust_actual - cust_expected, 2),
            "status":           cust_status,
            "devices":          device_rows,
            # Quantity reconciliation fields
            "myAdminTotal":     cust_myadmin_total,
            "qbTotal":          cust_qb_total if has_qb_data else None,
            "qtyDelta":         (cust_myadmin_total - cust_qb_total) if has_qb_data else None,
            "qtyMatch":         cust_qty_match,
            "qtyUnderBilled":   cust_qty_under,
            "qtyOverBilled":    cust_qty_over,
            "qtyMissing":       cust_qty_missing,
            "hasQbData":        has_qb_data,
            "skuQtyBreakdown":  qty_rows,
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
