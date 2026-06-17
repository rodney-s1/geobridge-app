"""
reconciliation.py -- Billing Reconciliation API
===============================================
Compares what MyAdmin says customers have (devices + rate plan codes)
against the QB SKU catalog and per-customer price overrides to surface
billing discrepancies.

Key logic:
  1. Load all cached MyAdmin contracts (promoCode per device).
  2. For each device, look up promoCode -> skuKey via sku_mappings.
  3. Look up the expected price:
       - customer-specific override (from sku_customer_overrides) if it exists
       - else catalog default price
  4. Look up the actual QB-invoiced price from sku_customer_overrides
     (since that's where invoice import stored per-customer prices).
  5. Emit per-device rows and per-customer summary rows with status:
       ok         -- expected == actual
       over        -- actual > expected  (customer billed more than catalog)
       under       -- actual < expected  (customer billed less than catalog)
       unmapped    -- promoCode has no SKU mapping
       no_price    -- SKU exists but no price anywhere
       not_in_qb   -- customer has no QB data / no invoiced price
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
    catalog   = _load(os.path.join(_HERE, "sku_catalog.json"),            [])
    mappings  = _load(os.path.join(_HERE, "sku_mappings.json"),           [])
    overrides = _load(os.path.join(_HERE, "sku_customer_overrides.json"), [])
    return catalog, mappings, overrides


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
    catalog, mappings, overrides = _get_stores()

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

    # promoCode (upper) -> skuKey
    mapping_index: Dict[str, str] = {
        (m.get("promoCode") or "").upper(): m.get("skuKey") or ""
        for m in mappings
    }

    # (norm_customerName, skuKey) -> price
    ovr_index: Dict[tuple, float] = {
        (_normalize(o["customerName"]), o["skuKey"]): float(o.get("price") or 0)
        for o in overrides
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

        rate_plan = (c.get("promoCode") or "").upper()

        if cid not in company_map:
            company_map[cid] = {"customerId": cid, "customerName": cname, "devices": []}
        if not company_map[cid]["customerName"] and cname:
            company_map[cid]["customerName"] = cname

        company_map[cid]["devices"].append({
            "serialNumber": serial,
            "ratePlanCode": rate_plan,
        })

    # -- Per-device reconciliation ---------------------------------------------
    STATUS_PRIORITY = {"discrepancy": 0, "unmapped": 1, "no_price": 2, "not_in_qb": 3, "ok": 4}

    result_customers = []

    total_ok = total_over = total_under = total_unmapped = 0
    total_no_price = total_not_in_qb = 0
    total_expected = total_actual = 0.0

    for cid, cdata in company_map.items():
        cname   = cdata["customerName"]
        devices = cdata["devices"]

        cust_ok = cust_over = cust_under = cust_unmapped = cust_no_price = 0
        cust_expected = cust_actual = 0.0
        device_rows = []

        for dev in devices:
            rate_plan = dev["ratePlanCode"]
            sku_key   = mapping_index.get(rate_plan, "")

            if not rate_plan or rate_plan not in mapping_index:
                # No mapping at all
                device_rows.append({
                    "serialNumber": dev["serialNumber"],
                    "ratePlanCode": rate_plan or "(none)",
                    "skuKey":       "",
                    "skuName":      "",
                    "expectedPrice": None,
                    "actualPrice":   None,
                    "delta":         None,
                    "priceSource":   "none",
                    "status":        "unmapped",
                })
                cust_unmapped += 1
                continue

            if not sku_key:
                # Mapping exists but no SKU assigned yet
                device_rows.append({
                    "serialNumber": dev["serialNumber"],
                    "ratePlanCode": rate_plan,
                    "skuKey":       "",
                    "skuName":      "",
                    "expectedPrice": None,
                    "actualPrice":   None,
                    "delta":         None,
                    "priceSource":   "none",
                    "status":        "unmapped",
                })
                cust_unmapped += 1
                continue

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
        },
        "customers": result_customers,
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
