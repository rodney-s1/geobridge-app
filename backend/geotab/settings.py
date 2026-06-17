"""
settings.py — QB SKU / Rate-Plan mapping backend
=================================================
Persists two files next to this module:

  sku_catalog.json      — list of QB SKU objects discovered from invoice imports
  sku_mappings.json     — list of {ratePlanCode, skuKey, defaultPrice, notes} rows

Endpoints:
  GET  /api/settings/sku-catalog              list all known SKUs
  POST /api/settings/sku-catalog              upsert a SKU (key, fullPath, defaultPrice)
  DELETE /api/settings/sku-catalog/{sku_key}  remove a SKU

  GET  /api/settings/sku-mappings             list rate-plan → SKU mappings
  POST /api/settings/sku-mappings             upsert a mapping
  DELETE /api/settings/sku-mappings/{rate_plan_code}  remove a mapping

  GET  /api/settings/customer-overrides            list per-customer price overrides
  POST /api/settings/customer-overrides            upsert {customerName, skuKey, price}
  DELETE /api/settings/customer-overrides/{id}     remove override by id (customerName|skuKey)

  POST /api/settings/import-qb-skus           parse uploaded QB CSV → populate sku_catalog
  GET  /api/settings/unmapped-rate-plans       rate plan codes seen in MyAdmin with no mapping
"""

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional
import csv
import io
import json
import os
import re
import time

router = APIRouter()

# ─── Disk paths ──────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
SKU_CATALOG_FILE        = os.path.join(_HERE, "sku_catalog.json")
SKU_MAPPINGS_FILE       = os.path.join(_HERE, "sku_mappings.json")
CUSTOMER_OVERRIDES_FILE = os.path.join(_HERE, "sku_customer_overrides.json")


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ─── In-memory stores ─────────────────────────────────────────────────────────
# sku_catalog:  list[dict]  — {skuKey, fullPath, defaultPrice, category}
# sku_mappings: list[dict]  — {ratePlanCode, skuKey, defaultPrice, notes}
# cust_ovr:     list[dict]  — {id, customerName, skuKey, price}
sku_catalog:  list = _load(SKU_CATALOG_FILE,        [])
sku_mappings: list = _load(SKU_MAPPINGS_FILE,       [])
cust_ovr:     list = _load(CUSTOMER_OVERRIDES_FILE, [])

print(f"[settings] SKU catalog: {len(sku_catalog)} SKUs, "
      f"{len(sku_mappings)} mappings, {len(cust_ovr)} overrides")


# ════════════════════════════════════════════════════════════════════════════════
#  SKU CATALOG
# ════════════════════════════════════════════════════════════════════════════════

class SkuUpsert(BaseModel):
    skuKey: str
    fullPath: str
    defaultPrice: float = 0.0
    category: str = ""
    desc: str = ""


@router.get("/settings/sku-catalog")
async def list_sku_catalog():
    return sorted(sku_catalog, key=lambda x: x.get("skuKey", "").lower())


@router.post("/settings/sku-catalog")
async def upsert_sku(body: SkuUpsert):
    global sku_catalog
    existing = next((s for s in sku_catalog if s["skuKey"] == body.skuKey), None)
    if existing:
        existing.update(body.dict())
    else:
        sku_catalog.append(body.dict())
    _save(SKU_CATALOG_FILE, sku_catalog)
    return {"success": True, "sku": body.dict()}


@router.delete("/settings/sku-catalog/{sku_key:path}")
async def delete_sku(sku_key: str):
    global sku_catalog
    before = len(sku_catalog)
    sku_catalog = [s for s in sku_catalog if s["skuKey"] != sku_key]
    _save(SKU_CATALOG_FILE, sku_catalog)
    return {"success": True, "removed": before - len(sku_catalog)}


# ════════════════════════════════════════════════════════════════════════════════
#  SKU MAPPINGS  (rate plan code → QB SKU)
# ════════════════════════════════════════════════════════════════════════════════

class MappingUpsert(BaseModel):
    ratePlanCode: str
    skuKey: str
    defaultPrice: float = 0.0
    notes: str = ""


@router.get("/settings/sku-mappings")
async def list_sku_mappings():
    return sorted(sku_mappings, key=lambda x: x.get("ratePlanCode", "").lower())


@router.post("/settings/sku-mappings")
async def upsert_mapping(body: MappingUpsert):
    global sku_mappings
    existing = next((m for m in sku_mappings if m["ratePlanCode"].upper() == body.ratePlanCode.upper()), None)
    data = body.dict()
    data["ratePlanCode"] = data["ratePlanCode"].upper()
    if existing:
        existing.update(data)
    else:
        sku_mappings.append(data)
    _save(SKU_MAPPINGS_FILE, sku_mappings)
    return {"success": True, "mapping": data}


@router.delete("/settings/sku-mappings/{rate_plan_code:path}")
async def delete_mapping(rate_plan_code: str):
    global sku_mappings
    before = len(sku_mappings)
    sku_mappings = [m for m in sku_mappings if m["ratePlanCode"].upper() != rate_plan_code.upper()]
    _save(SKU_MAPPINGS_FILE, sku_mappings)
    return {"success": True, "removed": before - len(sku_mappings)}


# ════════════════════════════════════════════════════════════════════════════════
#  CUSTOMER PRICE OVERRIDES
# ════════════════════════════════════════════════════════════════════════════════

class OverrideUpsert(BaseModel):
    customerName: str
    skuKey: str
    price: float


def _ovr_id(customer_name: str, sku_key: str) -> str:
    return f"{customer_name}|{sku_key}"


@router.get("/settings/customer-overrides")
async def list_overrides():
    return sorted(cust_ovr, key=lambda x: x.get("customerName", "").lower())


@router.post("/settings/customer-overrides")
async def upsert_override(body: OverrideUpsert):
    global cust_ovr
    oid = _ovr_id(body.customerName, body.skuKey)
    existing = next((o for o in cust_ovr if o["id"] == oid), None)
    data = {**body.dict(), "id": oid}
    if existing:
        existing.update(data)
    else:
        cust_ovr.append(data)
    _save(CUSTOMER_OVERRIDES_FILE, cust_ovr)
    return {"success": True, "override": data}


@router.delete("/settings/customer-overrides/{override_id:path}")
async def delete_override(override_id: str):
    global cust_ovr
    before = len(cust_ovr)
    cust_ovr = [o for o in cust_ovr if o["id"] != override_id]
    _save(CUSTOMER_OVERRIDES_FILE, cust_ovr)
    return {"success": True, "removed": before - len(cust_ovr)}


# ════════════════════════════════════════════════════════════════════════════════
#  IMPORT QB INVOICE CSV  →  populate sku_catalog (and customer overrides)
# ════════════════════════════════════════════════════════════════════════════════

def _parse_item(item_str: str) -> tuple[str, str, str]:
    """
    Parse a QB Item (col P) string into (group, sku_name, desc).

    Format:  'Geotab Service:Service Fee Geotab (HOS V2) (Service Fee Geotab (HOS))'
      group    = 'Geotab Service'           (text before first ':')
      sku_name = 'Service Fee Geotab (HOS V2)'  (after ':', strip last top-level parens)
      desc     = 'Service Fee Geotab (HOS)'     (content of that last top-level parens)

    Uses a right-to-left balanced-paren scan so nested parens inside SKU names
    (e.g. 'DM Service Fee (Periodic)') are preserved correctly.
    """
    item_str = item_str.strip()

    # Split off the group prefix
    if ':' in item_str:
        colon    = item_str.index(':')
        group    = item_str[:colon].strip()
        rest     = item_str[colon + 1:].strip()
    else:
        group = ''
        rest  = item_str

    desc     = ''
    sku_name = rest

    # Strip the last top-level (...) to get the clean SKU name
    if rest.endswith(')'):
        depth = 0
        i = len(rest) - 1
        while i >= 0:
            if rest[i] == ')':
                depth += 1
            elif rest[i] == '(':
                depth -= 1
                if depth == 0:
                    desc     = rest[i + 1:-1].strip()
                    sku_name = rest[:i].strip()
                    break
            i -= 1

    return group, sku_name, desc


def _parse_qb_csv(content: str) -> dict:
    """
    Parse QB invoice CSV (doubled-comma format).
    Col 5=Type, Col 13=Name, Col 15=Item (full QB path), Col 17=Qty, Col 19=Sales Price

    Returns {skus: {sku_name: {...}}, customers: {parent_name: {sku_name: price}}}
    """
    reader = csv.reader(io.StringIO(content))
    rows   = list(reader)

    skus_out:      dict = {}
    customers_out: dict = {}

    for row in rows:
        if len(row) <= 19:
            continue
        if row[5].strip() != 'Invoice':
            continue
        item_raw  = row[15].strip()
        name_raw  = row[13].strip()
        price_raw = row[19].strip()

        if not item_raw or not name_raw:
            continue
        if '%' in price_raw:
            continue

        try:
            price = float(price_raw)
        except ValueError:
            continue

        group, sku_name, desc = _parse_item(item_raw)

        if not sku_name:
            continue

        if sku_name not in skus_out:
            skus_out[sku_name] = {
                'skuKey':        sku_name,
                'fullPath':      item_raw,   # full col P value kept for reference
                'defaultPrice':  price,
                'category':      group,
                'desc':          desc,
                'prices':        set(),
                'customerCount': 0,
            }
        skus_out[sku_name]['prices'].add(price)
        skus_out[sku_name]['customerCount'] += 1

        # Parent customer name (strip child sub-account after ':')
        parent_name = name_raw.split(':')[0].strip()
        if parent_name not in customers_out:
            customers_out[parent_name] = {}
        customers_out[parent_name][sku_name] = price

    # Convert price sets to sorted lists
    for v in skus_out.values():
        v['prices'] = sorted(v['prices'])

    return {'skus': skus_out, 'customers': customers_out}


@router.post("/settings/import-qb-skus")
async def import_qb_skus(file: UploadFile = File(...)):
    """Upload a QB invoice CSV to auto-populate the SKU catalog and customer price overrides."""
    global sku_catalog, cust_ovr

    content = (await file.read()).decode("utf-8-sig", errors="replace")
    parsed  = _parse_qb_csv(content)

    skus_added   = 0
    skus_updated = 0
    ovr_added    = 0
    ovr_updated  = 0

    # ── Upsert SKU catalog ────────────────────────────────────────────────────
    for sku_key, data in parsed['skus'].items():
        existing = next((s for s in sku_catalog if s['skuKey'] == sku_key), None)
        entry = {
            'skuKey':       data['skuKey'],
            'fullPath':     data['fullPath'],
            'defaultPrice': data['defaultPrice'],
            'category':     data['category'],
            'desc':         data.get('desc', ''),
        }
        if existing:
            existing.update(entry)
            skus_updated += 1
        else:
            sku_catalog.append(entry)
            skus_added += 1

    _save(SKU_CATALOG_FILE, sku_catalog)

    # ── Upsert customer price overrides ──────────────────────────────────────
    for cust_name, skus in parsed['customers'].items():
        for sku_key, price in skus.items():
            oid = _ovr_id(cust_name, sku_key)
            existing = next((o for o in cust_ovr if o['id'] == oid), None)
            data_row = {
                'id':           oid,
                'customerName': cust_name,
                'skuKey':       sku_key,
                'price':        price,
            }
            if existing:
                existing.update(data_row)
                ovr_updated += 1
            else:
                cust_ovr.append(data_row)
                ovr_added += 1

    _save(CUSTOMER_OVERRIDES_FILE, cust_ovr)

    return {
        "success":      True,
        "skusAdded":    skus_added,
        "skusUpdated":  skus_updated,
        "ovrAdded":     ovr_added,
        "ovrUpdated":   ovr_updated,
        "totalSkus":    len(sku_catalog),
        "totalCustomers": len(parsed['customers']),
    }


# ════════════════════════════════════════════════════════════════════════════════
#  UNMAPPED RATE PLANS  (rate plan codes in MyAdmin with no SKU mapping)
# ════════════════════════════════════════════════════════════════════════════════

@router.get("/settings/unmapped-rate-plans")
async def get_unmapped_rate_plans():
    """
    Compare rate plan codes seen in the MyAdmin sync cache against sku_mappings.
    Returns codes that have no mapping yet.
    """
    # Import here to avoid circular import
    from . import customers as cust_mod

    raw = cust_mod._sync_cache.get("raw_customers") or []
    if not raw:
        return {"unmapped": [], "total": 0, "hasCachedData": False}

    # Collect unique rate plan codes from all devices
    seen_codes: set = set()
    for c in raw:
        for d in (c.get("devices") or []):
            code = (d.get("promoCode") or "").strip().upper()
            if code:
                seen_codes.add(code)

    mapped_codes = {m["ratePlanCode"].upper() for m in sku_mappings}
    unmapped = sorted(seen_codes - mapped_codes)

    # Count devices per code for context
    code_counts: dict = {}
    for c in raw:
        for d in (c.get("devices") or []):
            code = (d.get("promoCode") or "").strip().upper()
            if code in seen_codes:
                code_counts[code] = code_counts.get(code, 0) + 1

    return {
        "unmapped": [
            {"ratePlanCode": code, "deviceCount": code_counts.get(code, 0)}
            for code in unmapped
        ],
        "total": len(unmapped),
        "hasCachedData": True,
    }


# ════════════════════════════════════════════════════════════════════════════════
#  SUMMARY  (for Settings page header cards)
# ════════════════════════════════════════════════════════════════════════════════

@router.get("/settings/summary")
async def get_settings_summary():
    from . import customers as cust_mod

    raw = cust_mod._sync_cache.get("raw_customers") or []
    seen_codes: set = set()
    for c in raw:
        for d in (c.get("devices") or []):
            code = (d.get("promoCode") or "").strip().upper()
            if code:
                seen_codes.add(code)

    mapped_codes = {m["ratePlanCode"].upper() for m in sku_mappings}
    unmapped_count = len(seen_codes - mapped_codes)

    return {
        "skuCount":       len(sku_catalog),
        "mappingCount":   len(sku_mappings),
        "overrideCount":  len(cust_ovr),
        "unmappedCount":  unmapped_count,
        "hasCachedData":  bool(raw),
    }
