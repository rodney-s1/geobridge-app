"""
settings.py -- QB SKU / Rate-Plan mapping backend
=================================================
Persists two files next to this module:

  sku_catalog.json      -- list of QB SKU objects discovered from invoice imports
  sku_mappings.json     -- list of {ratePlanCode, skuKey, defaultPrice, notes} rows

Endpoints:
  GET  /api/settings/sku-catalog              list all known SKUs
  POST /api/settings/sku-catalog              upsert a SKU (key, fullPath, defaultPrice)
  DELETE /api/settings/sku-catalog/{sku_key}  remove a SKU

  GET  /api/settings/sku-mappings             list rate-plan -> SKU mappings
  POST /api/settings/sku-mappings             upsert a mapping
  DELETE /api/settings/sku-mappings/{rate_plan_code}  remove a mapping

  GET  /api/settings/customer-overrides            list per-customer price overrides
  POST /api/settings/customer-overrides            upsert {customerName, skuKey, price}
  DELETE /api/settings/customer-overrides/{id}     remove override by id (customerName|skuKey)

  POST /api/settings/import-qb-skus           parse uploaded QB CSV -> populate sku_catalog
  GET  /api/settings/unmapped-rate-plans       rate plan codes seen in MyAdmin with no mapping
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, Dict, List, Tuple
import csv
import io
import json
import os
import re
import time

# S3 sync -- imported lazily so the app still works if boto3 is missing
try:
    from geotab.s3_sync import upload_file_async as _s3_push
except Exception:
    def _s3_push(filename: str) -> None:  # type: ignore
        pass

from .auth import require_session

router = APIRouter(dependencies=[Depends(require_session)])

# --- Disk paths — mutable user data lives in _DATA_DIR (survives reinstalls) -
# See _data_dir.py for env var resolution and first-run migration logic.
from ._data_dir import _DATA_DIR, _HERE
SKU_CATALOG_FILE        = os.path.join(_DATA_DIR, "sku_catalog.json")
SKU_MAPPINGS_FILE       = os.path.join(_DATA_DIR, "sku_mappings.json")
CUST_RATE_PLAN_FILE     = os.path.join(_DATA_DIR, "customer_rate_plan_mappings.json")
CUSTOMER_OVERRIDES_FILE = os.path.join(_DATA_DIR, "sku_customer_overrides.json")
QB_QUANTITIES_FILE          = os.path.join(_DATA_DIR, "qb_invoice_quantities.json")
MYADMIN_CACHE_FILE          = os.path.join(_DATA_DIR, "myadmin_cache.json")
SERIAL_PREFIX_FILE          = os.path.join(_DATA_DIR, "serial_prefix_mappings.json")
QB_AUTH_SKUS_FILE           = os.path.join(_DATA_DIR, "qb_authoritative_skus.json")


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Silently return default for known optional files
        return default
    except json.JSONDecodeError as e:
        print(f"[settings] JSON decode error in {path}: {e}")
        return default
    except Exception as e:
        print(f"[settings] Unexpected error loading {path}: {e}")
        return default


def _save(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        filename = os.path.basename(path)
        print(f"[settings] Saved {len(data)} items to {path}")
        # Mirror to S3 in a background thread -- never blocks the response
        _s3_push(filename)
    except Exception as e:
        print(f"[settings] ERROR saving {path}: {e}")
        raise


# --- In-memory stores ---------------------------------------------------------
# These are loaded from disk on every read operation (GET endpoints) to avoid
# stale-cache issues when the JSON files are updated externally (e.g. after a
# git pull or a previous import before the process was restarted).
#
# Write operations (POST/DELETE/import) keep them in sync in memory too so that
# multiple writes in the same request cycle are consistent.
#
# For a local desktop app with <1000 items in each file, disk reads on every
# GET are effectively instant and far safer than module-level caching.

def _catalog()          -> list: return _load(SKU_CATALOG_FILE,        [])
def _serial_prefixes()  -> list: return _load(SERIAL_PREFIX_FILE,      [])
def _qb_auth_skus()     -> list: return _load(QB_AUTH_SKUS_FILE,        [])
def _cust_mappings()    -> list:
    data = _load(CUST_RATE_PLAN_FILE, None)
    if data is None:
        # First run on this machine -- create the file so S3 can pick it up
        _save(CUST_RATE_PLAN_FILE, [])
        return []
    return data
def _overrides()     -> list: return _load(CUSTOMER_OVERRIDES_FILE,[])
def _myadmin_cache() -> dict: return _load(MYADMIN_CACHE_FILE,     {})


def _normalize_mapping(m: dict) -> dict:
    """
    Normalise a sku_mappings.json entry to the canonical schema:
      { ratePlanCode, skuKey, skuKeys, defaultPrice, notes }

    skuKeys is an ordered list of QB SKU names for this rate plan code.
    The first entry (skuKeys[0]) is the primary invoice SKU; all entries
    are treated as valid QB matches during reconciliation.
    skuKey is kept in sync with skuKeys[0] for backward compatibility.

    Handles legacy schemas:
      Old (generated by the sync import):
        { id, promoCode, skuKey, label }   -- label was always == promoCode, discard it
      Old (qbAliases):                     -- migrated to skuKeys automatically
        { ratePlanCode, skuKey, qbAliases: [...] }
      New:
        { ratePlanCode, skuKey, skuKeys, defaultPrice, notes }
    """
    code = (m.get("ratePlanCode") or m.get("promoCode") or m.get("id") or "").strip().upper()
    raw_notes = m.get("notes") or m.get("label") or ""
    notes = "" if raw_notes.strip().upper() == code else raw_notes

    # Build skuKeys — prefer explicit skuKeys list, otherwise migrate from skuKey + qbAliases
    raw_sku_keys = m.get("skuKeys") or []
    primary_sku  = m.get("skuKey") or ""
    if raw_sku_keys:
        # Ensure primary_sku is consistent with skuKeys[0]
        sku_keys = [k for k in raw_sku_keys if isinstance(k, str) and k.strip()]
    elif primary_sku:
        # Migrate: skuKey + optional qbAliases -> skuKeys
        aliases = [a for a in (m.get("qbAliases") or []) if isinstance(a, str) and a.strip()]
        sku_keys = [primary_sku] + [a for a in aliases if a != primary_sku]
    else:
        sku_keys = []

    primary = sku_keys[0] if sku_keys else ""
    result = {
        "ratePlanCode": code,
        "skuKey":       primary,   # backward-compat: always == skuKeys[0]
        "skuKeys":      sku_keys,
        "defaultPrice": float(m.get("defaultPrice") or 0),
        "notes":        notes,
    }
    # Preserve optional fields
    if m.get("planLevel"):
        result["planLevel"] = m["planLevel"]
    if m.get("cost") is not None:
        result["cost"] = float(m["cost"])
    return result


def _mappings() -> list:
    """Load sku_mappings.json and normalise every entry to the canonical schema.
    If any entry needed migration (old promoCode/id schema, or notes==code),
    re-save the file so the migration only happens once.
    """
    raw = _load(SKU_MAPPINGS_FILE, [])
    normalised = [_normalize_mapping(m) for m in raw]
    # Needs migration if: old schema (no ratePlanCode key) OR notes still equal to code
    def _needs_fix(m):
        if "ratePlanCode" not in m:
            return True
        raw_notes = (m.get("notes") or m.get("label") or "").strip().upper()
        code = (m.get("ratePlanCode") or "").strip().upper()
        return raw_notes == code and raw_notes != ""
    needs_migration = any(_needs_fix(m) for m in raw)
    if needs_migration and normalised:
        print(f"[settings] Migrating sku_mappings.json: "
              f"{len(normalised)} entries normalised to ratePlanCode schema")
        _save(SKU_MAPPINGS_FILE, normalised)
    return normalised

# Keep module-level references for write operations so imports/upserts
# don't need to reload from disk mid-operation.
sku_catalog:      list = _catalog()
sku_mappings:     list = _mappings()
cust_rate_plans:  list = _cust_mappings()
cust_ovr:         list = _overrides()
serial_prefixes:  list = _serial_prefixes()

print(f"[settings] SKU catalog: {len(sku_catalog)} SKUs, "
      f"{len(sku_mappings)} global mappings, "
      f"{len(cust_rate_plans)} customer-specific mappings, "
      f"{len(cust_ovr)} price overrides, "
      f"{len(serial_prefixes)} serial prefix mappings")


# ================================================================================
#  SKU CATALOG
# ================================================================================

class SkuUpsert(BaseModel):
    skuKey: str
    fullPath: str
    defaultPrice: float = 0.0
    category: str = ""
    desc: str = ""
    cost: float = 0.0


@router.get("/settings/sku-catalog")
async def list_sku_catalog():
    # Always read from disk so stale in-memory state is never returned
    data = _catalog()
    return sorted(data, key=lambda x: x.get("skuKey", "").lower())


@router.post("/settings/sku-catalog")
async def upsert_sku(body: SkuUpsert):
    global sku_catalog
    sku_catalog = _catalog()   # reload from disk first
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
    sku_catalog = _catalog()   # reload from disk first
    before = len(sku_catalog)
    sku_catalog = [s for s in sku_catalog if s["skuKey"] != sku_key]
    _save(SKU_CATALOG_FILE, sku_catalog)
    return {"success": True, "removed": before - len(sku_catalog)}


# ================================================================================
#  SKU MAPPINGS  (rate plan code -> QB SKU)
# ================================================================================

class MappingUpsert(BaseModel):
    ratePlanCode: str
    skuKeys: list       # ordered list of QB SKU names; first = primary invoice SKU
    defaultPrice: float = 0.0
    notes: str = ""


@router.get("/settings/sku-mappings")
async def list_sku_mappings():
    data = _mappings()
    return sorted(data, key=lambda x: x.get("ratePlanCode", "").lower())


@router.post("/settings/sku-mappings")
async def upsert_mapping(body: MappingUpsert):
    global sku_mappings
    sku_mappings = _mappings()  # reload from disk first
    existing = next((m for m in sku_mappings if m["ratePlanCode"].upper() == body.ratePlanCode.upper()), None)
    sku_keys = [k for k in body.skuKeys if isinstance(k, str) and k.strip()]
    data = {
        "ratePlanCode": body.ratePlanCode.upper(),
        "skuKey":       sku_keys[0] if sku_keys else "",   # backward-compat
        "skuKeys":      sku_keys,
        "defaultPrice": body.defaultPrice,
        "notes":        body.notes,
    }
    if existing:
        # Preserve planLevel and cost if they exist and weren't sent
        for field in ("planLevel", "cost"):
            if field in existing and field not in data:
                data[field] = existing[field]
        existing.update(data)
    else:
        sku_mappings.append(data)
    _save(SKU_MAPPINGS_FILE, sku_mappings)
    return {"success": True, "mapping": data}


@router.delete("/settings/sku-mappings/{rate_plan_code:path}")
async def delete_mapping(rate_plan_code: str):
    global sku_mappings
    sku_mappings = _mappings()  # reload from disk first
    before = len(sku_mappings)
    sku_mappings = [m for m in sku_mappings if m["ratePlanCode"].upper() != rate_plan_code.upper()]
    _save(SKU_MAPPINGS_FILE, sku_mappings)
    return {"success": True, "removed": before - len(sku_mappings)}


# ================================================================================
#  CUSTOMER-SPECIFIC RATE PLAN MAPPINGS
#  { id, customerName, ratePlanCode, skuKey, defaultPrice, notes }
#  Lookup: (customer, ratePlanCode) -> skuKey overrides the global mapping.
#  Used by reconciliation as the highest-priority tier.
# ================================================================================

class CustRatePlanUpsert(BaseModel):
    customerName: str
    ratePlanCode: str
    skuKey: str
    defaultPrice: float = 0.0
    notes: str = ""


def _crp_id(customer_name: str, rate_plan_code: str) -> str:
    return f"{customer_name.strip()}|{rate_plan_code.strip().upper()}"


@router.get("/settings/customer-rate-plan-mappings")
async def list_cust_rate_plan_mappings():
    data = _cust_mappings()
    return sorted(data, key=lambda x: (
        x.get("customerName", "").lower(),
        x.get("ratePlanCode", "").lower()
    ))


@router.post("/settings/customer-rate-plan-mappings")
async def upsert_cust_rate_plan(body: CustRatePlanUpsert):
    global cust_rate_plans
    cust_rate_plans = _cust_mappings()
    oid = _crp_id(body.customerName, body.ratePlanCode)
    existing = next((m for m in cust_rate_plans if m["id"] == oid), None)
    data = {
        "id":            oid,
        "customerName":  body.customerName.strip(),
        "ratePlanCode":  body.ratePlanCode.strip().upper(),
        "skuKey":        body.skuKey,
        "defaultPrice":  body.defaultPrice,
        "notes":         body.notes,
    }
    if existing:
        existing.update(data)
    else:
        cust_rate_plans.append(data)
    _save(CUST_RATE_PLAN_FILE, cust_rate_plans)
    return {"success": True, "mapping": data}


@router.delete("/settings/customer-rate-plan-mappings/{mapping_id:path}")
async def delete_cust_rate_plan(mapping_id: str):
    global cust_rate_plans
    cust_rate_plans = _cust_mappings()
    before = len(cust_rate_plans)
    cust_rate_plans = [m for m in cust_rate_plans if m["id"] != mapping_id]
    _save(CUST_RATE_PLAN_FILE, cust_rate_plans)
    return {"success": True, "removed": before - len(cust_rate_plans)}


# ================================================================================
#  CUSTOMER PRICE OVERRIDES
# ================================================================================

class OverrideUpsert(BaseModel):
    customerName: str
    skuKey: str
    price: float


def _ovr_id(customer_name: str, sku_key: str) -> str:
    return f"{customer_name}|{sku_key}"


@router.get("/settings/customer-overrides")
async def list_overrides():
    data = _overrides()
    return sorted(data, key=lambda x: x.get("customerName", "").lower())


@router.post("/settings/customer-overrides")
async def upsert_override(body: OverrideUpsert):
    global cust_ovr
    cust_ovr = _overrides()    # reload from disk first
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
    cust_ovr = _overrides()    # reload from disk first
    before = len(cust_ovr)
    cust_ovr = [o for o in cust_ovr if o["id"] != override_id]
    _save(CUSTOMER_OVERRIDES_FILE, cust_ovr)
    return {"success": True, "removed": before - len(cust_ovr)}


# ================================================================================
#  SERIAL PREFIX MAPPINGS  (serial prefix -> QB SKU)
#  { prefix, skuKey, notes, dmExcluded }
#  Used in invoices._sku_from_serial() and reconciliation.py Tier 0.5x tiers.
#  dmExcluded=true entries are excluded from prorated invoice calculations.
# ================================================================================

class SerialPrefixUpsert(BaseModel):
    prefix:     str
    skuKey:     str = ""
    notes:      str = ""
    dmExcluded: bool = False


@router.get("/settings/serial-prefix-mappings")
async def list_serial_prefix_mappings():
    data = _serial_prefixes()
    return sorted(data, key=lambda x: x.get("prefix", "").upper())


@router.post("/settings/serial-prefix-mappings")
async def upsert_serial_prefix(body: SerialPrefixUpsert):
    global serial_prefixes
    serial_prefixes = _serial_prefixes()   # reload from disk first
    norm_prefix = body.prefix.strip().upper()
    if not norm_prefix:
        raise HTTPException(status_code=400, detail="prefix must not be empty")
    existing = next((p for p in serial_prefixes if p["prefix"].upper() == norm_prefix), None)
    data = {
        "prefix":     norm_prefix,
        "skuKey":     body.skuKey.strip(),
        "notes":      body.notes.strip(),
        "dmExcluded": body.dmExcluded,
    }
    if existing:
        existing.update(data)
    else:
        serial_prefixes.append(data)
    _save(SERIAL_PREFIX_FILE, serial_prefixes)
    return {"success": True, "mapping": data}


@router.delete("/settings/serial-prefix-mappings/{prefix:path}")
async def delete_serial_prefix(prefix: str):
    global serial_prefixes
    serial_prefixes = _serial_prefixes()   # reload from disk first
    norm_prefix = prefix.strip().upper()
    before = len(serial_prefixes)
    serial_prefixes = [p for p in serial_prefixes if p["prefix"].upper() != norm_prefix]
    _save(SERIAL_PREFIX_FILE, serial_prefixes)
    return {"success": True, "removed": before - len(serial_prefixes)}


# ================================================================================
#  QB AUTHORITATIVE SKUS  (skuKey + optional notes)
#  These SKUs use QB invoice qty as ground truth and are excluded from the
#  MyAdmin vs QB diff.  Stored in qb_authoritative_skus.json so they can be
#  maintained from the Settings UI without touching reconciliation.py.
# ================================================================================

class QbAuthSkuUpsert(BaseModel):
    skuKey: str
    notes:  str = ""


@router.get("/settings/qb-authoritative-skus")
async def list_qb_auth_skus():
    data = _qb_auth_skus()
    return sorted(data, key=lambda x: x.get("skuKey", "").lower())


@router.post("/settings/qb-authoritative-skus")
async def upsert_qb_auth_sku(body: QbAuthSkuUpsert):
    sku_key = body.skuKey.strip()
    if not sku_key:
        raise HTTPException(status_code=400, detail="skuKey must not be empty")
    data = _qb_auth_skus()
    existing = next((e for e in data if e["skuKey"] == sku_key), None)
    entry = {"skuKey": sku_key, "notes": body.notes.strip()}
    if existing:
        existing.update(entry)
    else:
        data.append(entry)
    _save(QB_AUTH_SKUS_FILE, data)
    return {"success": True, "entry": entry}


@router.delete("/settings/qb-authoritative-skus/{sku_key:path}")
async def delete_qb_auth_sku(sku_key: str):
    data = _qb_auth_skus()
    before = len(data)
    data = [e for e in data if e["skuKey"] != sku_key]
    _save(QB_AUTH_SKUS_FILE, data)
    return {"success": True, "removed": before - len(data)}


# ================================================================================
#  IMPORT QB INVOICE CSV  ->  populate sku_catalog (and customer overrides)
# ================================================================================

def _parse_item(item_str: str) -> Tuple[str, str, str]:
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


# Mapping of (group, sku_name) -> disambiguated skuKey for QB items where the
# same SKU name appears under two different QB groups with different meanings.
# Without this, e.g. "Surfsight Service:SS Service Fee" (standalone portal cameras,
# no MyAdmin counterpart) and "Geotab Service:SS Service Fee" (MyAdmin-tracked
# cameras) would both collapse to skuKey="SS Service Fee" and their QB quantities
# would be summed, producing a false over-billed result.
_QB_GROUP_SKU_REMAP: dict = {
    ("Surfsight Service", "SS Service Fee"): "SS Service Fee (Standalone)",
}


def _parse_qb_csv(content: str) -> dict:
    """
    Parse QB invoice CSV (doubled-comma format).
    Col 5=Type, Col 11=Memo, Col 13=Name, Col 15=Item (full QB path), Col 17=Qty, Col 19=Sales Price

    Line items whose Memo field (col 11 / spreadsheet column L) contains the
    phrase "new activations" (case-insensitive) are prorated activation charges
    posted alongside the regular monthly invoice.  They are excluded from the
    quantity accumulation so they don't inflate QB qty counts and produce false
    "over-billed" results in Reconciliation.  The SKU catalog and per-customer
    price overrides are still updated from these rows so catalog coverage is
    not affected.

    Returns {
      skus:       {sku_name: {...}},
      customers:  {parent_name: {sku_name: price}},
      quantities: {parent_name: {sku_name: total_qty}}   <-- NEW: summed Qty per customer+SKU
    }
    """
    reader = csv.reader(io.StringIO(content))
    rows   = list(reader)

    skus_out:       dict = {}
    customers_out:  dict = {}
    quantities_out: dict = {}   # parent_name -> {sku_name -> summed qty}

    for row in rows:
        if len(row) <= 19:
            continue
        if row[5].strip() != 'Invoice':
            continue
        item_raw  = row[15].strip()
        name_raw  = row[13].strip()
        price_raw = row[19].strip()
        qty_raw   = row[17].strip() if len(row) > 17 else ''
        memo_raw  = row[11].strip() if len(row) > 11 else ''

        if not item_raw or not name_raw:
            continue
        if '%' in price_raw:
            continue

        # Skip prorated "New Activations" line items — these are charged alongside
        # the regular monthly invoice for devices activated mid-period and must not
        # be counted toward the reconciliation QB quantity total.
        is_new_activation = 'new activations' in memo_raw.lower()

        try:
            price = float(price_raw.replace(',', ''))
        except ValueError:
            continue

        # Parse quantity -- QB exports commas in large numbers (e.g. "3,591.00")
        # and fractional qty sometimes; strip commas then round to nearest int.
        try:
            qty = max(1, round(float(qty_raw.replace(',', '')))) if qty_raw else 1
        except ValueError:
            qty = 1

        group, sku_name, desc = _parse_item(item_raw)

        # Disambiguate SKU names that appear under multiple QB groups.
        sku_name = _QB_GROUP_SKU_REMAP.get((group, sku_name), sku_name)

        if not sku_name:
            continue

        # Always upsert SKU catalog so new SKUs seen only on activation invoices
        # are still discovered.  Price overrides are skipped for activation rows
        # because their prorated price is not the standard monthly rate.
        if sku_name not in skus_out:
            skus_out[sku_name] = {
                'skuKey':        sku_name,
                'fullPath':      item_raw,
                'defaultPrice':  price,
                'category':      group,
                'desc':          desc,
                'prices':        set(),
                'customerCount': 0,
            }
        skus_out[sku_name]['prices'].add(price)
        skus_out[sku_name]['customerCount'] += 1

        # QB Name column format: "Parent:Sub-account" (e.g. "Atrium Health:Atrium Health - Charlotte")
        # or plain "Customer Name" when there is no sub-account hierarchy.
        # We want the sub-account (right of colon) because that is the specific
        # billing entity that matches the MyAdmin company name.  For plain names
        # (no colon) fall back to the full name unchanged.
        _name_parts = name_raw.split(':')
        parent_name = _name_parts[-1].strip() if len(_name_parts) > 1 else _name_parts[0].strip()

        # Skip price overrides and quantity accumulation for New Activations rows.
        # Their prorated price would clobber the standard monthly rate, and their
        # qty would inflate the QB invoice count vs MyAdmin active devices.
        if is_new_activation:
            continue

        if parent_name not in customers_out:
            customers_out[parent_name] = {}
        customers_out[parent_name][sku_name] = price

        # Accumulate quantities
        if parent_name not in quantities_out:
            quantities_out[parent_name] = {}
        quantities_out[parent_name][sku_name] = (
            quantities_out[parent_name].get(sku_name, 0) + qty
        )

    # Convert price sets to sorted lists
    for v in skus_out.values():
        v['prices'] = sorted(v['prices'])

    return {'skus': skus_out, 'customers': customers_out, 'quantities': quantities_out}


@router.post("/settings/import-qb-skus")
async def import_qb_skus(file: UploadFile = File(...)):
    """Upload a QB invoice CSV to auto-populate the SKU catalog and customer price overrides."""
    global sku_catalog, cust_ovr, sku_mappings
    sku_catalog = _catalog()    # reload from disk before import
    cust_ovr    = _overrides()  # reload from disk before import

    content = (await file.read()).decode("utf-8-sig", errors="replace")
    parsed  = _parse_qb_csv(content)

    skus_added   = 0
    skus_updated = 0
    ovr_added    = 0
    ovr_updated  = 0

    # -- Upsert SKU catalog ----------------------------------------------------
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

    # Sync updated prices back into rate plan mappings
    sku_mappings = _mappings()   # reload from disk
    mappings_synced = _sync_mappings_from_catalog(sku_catalog, sku_mappings)
    if mappings_synced:
        _save(SKU_MAPPINGS_FILE, sku_mappings)

    # -- Upsert customer price overrides --------------------------------------
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

    # -- Save QB invoice quantities (customerName|skuKey -> qty) --------------
    # Flatten to a list of records for easy querying
    qty_records = []
    for cust_name, skus in parsed['quantities'].items():
        for sku_key, qty in skus.items():
            qty_records.append({
                'id':           f"{cust_name}|{sku_key}",
                'customerName': cust_name,
                'skuKey':       sku_key,
                'qbQty':        qty,
            })
    _save(QB_QUANTITIES_FILE, qty_records)

    total_qb_devices = sum(r['qbQty'] for r in qty_records)

    return {
        "success":         True,
        "skusAdded":       skus_added,
        "skusUpdated":     skus_updated,
        "ovrAdded":        ovr_added,
        "ovrUpdated":      ovr_updated,
        "totalSkus":       len(sku_catalog),
        "totalCustomers":  len(parsed['customers']),
        "totalQbDevices":  total_qb_devices,
        "mappingsSynced":  mappings_synced,
    }


@router.get("/settings/qb-quantities")
async def get_qb_quantities():
    """Return QB invoice quantities: [{customerName, skuKey, qbQty}]"""
    return _load(QB_QUANTITIES_FILE, [])


# ================================================================================
#  IMPORT ITEM PRICE LIST CSV  ->  fill $0 gaps + add new SKUs, never override
# ================================================================================

def _sync_mappings_from_catalog(catalog: list, mappings: list) -> int:
    """
    After a catalog import, push the updated defaultPrice and cost from the
    catalog back into sku_mappings entries that share the same skuKey.

    Only updates a mapping's defaultPrice when the catalog entry has a real
    (> 0) price — never zeros out a previously set price.

    Returns the number of mapping entries that were updated.
    """
    catalog_index = {s['skuKey']: s for s in catalog}
    updated = 0
    for m in mappings:
        cat = catalog_index.get(m.get('skuKey', ''))
        if not cat:
            continue
        changed = False
        if (cat.get('defaultPrice') or 0.0) > 0:
            if m.get('defaultPrice', 0.0) != cat['defaultPrice']:
                m['defaultPrice'] = cat['defaultPrice']
                changed = True
        if (cat.get('cost') or 0.0) > 0:
            if m.get('cost', 0.0) != cat['cost']:
                m['cost'] = cat['cost']
                changed = True
        if changed:
            updated += 1
    return updated


def _parse_price(s: str) -> Optional[float]:
    s = s.strip().replace(',', '')
    try:    return float(s)
    except: return None


def _parse_price_list_csv(content: str) -> List[dict]:
    """
    Parse a QB Item Price List CSV (doubled-comma format).
    Col 2 = Item (Group:SKU Name), Col 4 = Description, Col 6 = Cost, Col 8 = Price

    Skips parent/category rows (no ':' in Item column).
    Returns list of {skuKey, fullPath, category, desc, cost, defaultPrice}
    """
    reader = csv.reader(io.StringIO(content))
    rows   = list(reader)
    items  = []

    for row in rows[1:]:          # skip header
        if len(row) < 9:
            continue
        item_raw  = row[2].strip()
        desc_raw  = row[4].strip()
        cost_raw  = row[6].strip()
        price_raw = row[8].strip()

        if not item_raw or ':' not in item_raw:
            continue              # skip category parent rows

        price = _parse_price(price_raw)
        cost  = _parse_price(cost_raw)
        if price is None:
            continue

        colon    = item_raw.index(':')
        group    = item_raw[:colon].strip()
        sku_name = item_raw[colon + 1:].strip()
        if not sku_name:
            continue

        # Disambiguate SKU names that appear under multiple QB groups.
        sku_name = _QB_GROUP_SKU_REMAP.get((group, sku_name), sku_name)

        items.append({
            'skuKey':       sku_name,
            'fullPath':     item_raw,
            'category':     group,
            'desc':         desc_raw,
            'cost':         cost or 0.0,
            'defaultPrice': price,
        })

    return items


@router.post("/settings/import-price-list")
async def import_price_list(file: UploadFile = File(...)):
    """
    Upload a QB Item Price List CSV.

    Rules:
      - If SKU not in catalog -> ADD it (new SKU).
      - If SKU in catalog with defaultPrice == 0 -> UPDATE price (fill the gap).
      - If SKU in catalog with defaultPrice > 0  -> SKIP (never override
        customer-specific pricing derived from invoice imports).
    """
    global sku_catalog
    sku_catalog = _catalog()    # reload from disk before import

    content = (await file.read()).decode("utf-8-sig", errors="replace")
    items   = _parse_price_list_csv(content)

    if not items:
        raise HTTPException(status_code=400, detail="No valid items found in price list CSV.")

    catalog_index = {s['skuKey']: s for s in sku_catalog}

    added   = 0
    updated = 0
    skipped = 0

    for item in items:
        existing = catalog_index.get(item['skuKey'])
        if existing is None:
            # Brand-new SKU -- add to catalog
            new_entry = {
                'skuKey':       item['skuKey'],
                'fullPath':     item['fullPath'],
                'defaultPrice': item['defaultPrice'],
                'category':     item['category'],
                'desc':         item['desc'],
                'cost':         item['cost'],
            }
            sku_catalog.append(new_entry)
            catalog_index[item['skuKey']] = new_entry
            added += 1
        else:
            # Always update cost from the price list -- it's a separate field from price
            if item.get('cost'):
                existing['cost'] = item['cost']

            if (existing.get('defaultPrice') or 0.0) == 0.0 and item['defaultPrice'] > 0:
                # Existing SKU with $0 price -- fill the gap
                existing['defaultPrice'] = item['defaultPrice']
                if not existing.get('desc') and item.get('desc'):
                    existing['desc'] = item['desc']
                updated += 1
            else:
                # Existing SKU with a real price already -- do not override price
                skipped += 1

    sku_catalog.sort(key=lambda x: x.get('skuKey', '').lower())
    _save(SKU_CATALOG_FILE, sku_catalog)

    # Sync updated prices + costs back into rate plan mappings
    global sku_mappings
    sku_mappings = _mappings()   # reload from disk
    mappings_synced = _sync_mappings_from_catalog(sku_catalog, sku_mappings)
    if mappings_synced:
        _save(SKU_MAPPINGS_FILE, sku_mappings)

    return {
        "success":        True,
        "added":          added,
        "updated":        updated,
        "skipped":        skipped,
        "totalItems":     len(items),
        "totalSkus":      len(sku_catalog),
        "mappingsSynced": mappings_synced,
    }


# ================================================================================
#  UNMAPPED RATE PLANS  (rate plan codes in MyAdmin with no SKU mapping)
# ================================================================================

@router.get("/settings/unmapped-rate-plans")
async def get_unmapped_rate_plans():
    """
    Compare rate plan codes seen in the MyAdmin sync cache against sku_mappings.
    Returns per-code device counts and the list of affected customer names.
    Reads myadmin_cache.json directly -- no import of customers module needed.
    """
    try:
        cache = _myadmin_cache()
        raw   = cache.get("raw_customers") or []
        if not raw:
            return {"unmapped": [], "total": 0, "hasCachedData": False}

        seen_codes: set      = set()
        code_counts: dict    = {}
        code_customers: dict = {}   # code -> set of customer names

        for c in raw:
            cname = (c.get("companyName") or c.get("customerName") or "").strip()
            for d in (c.get("devices") or []):
                code = (d.get("promoCode") or "").strip().upper()
                if code:
                    seen_codes.add(code)
                    code_counts[code] = code_counts.get(code, 0) + 1
                    if cname:
                        code_customers.setdefault(code, set()).add(cname)

        mapped_codes = {m["ratePlanCode"].upper() for m in _mappings()}
        unmapped     = sorted(seen_codes - mapped_codes)

        return {
            "unmapped": [
                {
                    "ratePlanCode": code,
                    "deviceCount":  code_counts.get(code, 0),
                    # sorted alphabetically, capped at 15 to keep payload small
                    "customers":    sorted(code_customers.get(code, set()))[:15],
                    "customerCount": len(code_customers.get(code, set())),
                }
                for code in unmapped
            ],
            "total":        len(unmapped),
            "hasCachedData": True,
        }
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[settings] unmapped-rate-plans ERROR: {e}\n{tb}")
        return {"unmapped": [], "total": 0, "hasCachedData": False, "_error": str(e)}


# ================================================================================
#  SUMMARY  (for Settings page header cards)
# ================================================================================

@router.get("/settings/summary")
async def get_settings_summary():
    """
    Summary counts for the Settings page header cards.
    Reads myadmin_cache.json directly -- no import of customers module needed.
    """
    try:
        cache        = _myadmin_cache()
        raw          = cache.get("raw_customers") or []
        catalog_now       = _catalog()
        mappings_now      = _mappings()
        cust_maps_now     = _cust_mappings()
        overrides_now     = _overrides()

        seen_codes: set = set()
        for c in raw:
            for d in (c.get("devices") or []):
                code = (d.get("promoCode") or "").strip().upper()
                if code:
                    seen_codes.add(code)

        mapped_codes   = {m["ratePlanCode"].upper() for m in mappings_now}
        unmapped_count = len(seen_codes - mapped_codes)

        return {
            "skuCount":            len(catalog_now),
            "mappingCount":        len(mappings_now),
            "custMappingCount":    len(cust_maps_now),
            "overrideCount":       len(overrides_now),
            "serialPrefixCount":   len(_serial_prefixes()),
            "unmappedCount":       unmapped_count,
            "hasCachedData":       bool(raw),
        }
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[settings] summary ERROR: {e}\n{tb}")
        # Return disk counts even on error so the page still shows data
        try:
            return {
                "skuCount":          len(_catalog()),
                "mappingCount":      len(_mappings()),
                "custMappingCount":  len(_cust_mappings()),
                "overrideCount":     len(_overrides()),
                "serialPrefixCount": len(_serial_prefixes()),
                "unmappedCount":     0,
                "hasCachedData":     False,
                "_error": str(e),
            }
        except Exception:
            return {"skuCount": 0, "mappingCount": 0, "overrideCount": 0,
                    "unmappedCount": 0, "hasCachedData": False, "_error": str(e)}


# ===============================================================================
#  DEBUG  (open in browser to diagnose file-path / content issues)
# ===============================================================================

@router.get("/settings/debug")
async def debug_settings():
    """Diagnostic endpoint -- open http://127.0.0.1:8001/api/settings/debug in browser."""
    def file_info(path):
        exists = os.path.exists(path)
        size   = os.path.getsize(path) if exists else 0
        # Try to load and count items
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            data = json.loads(content)
            count = len(data)
            first = str(data[0])[:120] if data else "(empty list)"
            err   = None
        except FileNotFoundError:
            count = 0; first = None; err = "FileNotFoundError"; content = ""
        except json.JSONDecodeError as e:
            count = 0; first = None; err = f"JSONDecodeError: {e}"
        except Exception as e:
            count = 0; first = None; err = str(e)
        return {
            "path":    path,
            "exists":  exists,
            "bytes":   size,
            "count":   count,
            "first":   first,
            "error":   err,
            "preview": (content[:200] if exists else ""),
        }

    return {
        "version":        "schema-migration",   # git commit -- if you see this, new code is running
        "here":           _HERE,
        "data_dir":       _DATA_DIR,
        "cwd":            os.getcwd(),
        "catalog":        file_info(SKU_CATALOG_FILE),
        "mappings":       file_info(SKU_MAPPINGS_FILE),
        "overrides":      file_info(CUSTOMER_OVERRIDES_FILE),
        "serialPrefixes": file_info(SERIAL_PREFIX_FILE),
    }

# ===========================================================================
# S3 Sync endpoints
# ===========================================================================
# These are on a separate router with NO session dependency so the setup
# wizard can test credentials and save config before the user has logged in.
# ---------------------------------------------------------------------------

from fastapi import APIRouter as _APIRouter
s3_router = _APIRouter()   # no auth dependency — needed pre-login

try:
    from geotab import s3_sync as _s3
    _S3_AVAILABLE = True
except Exception:
    _S3_AVAILABLE = False


class S3ConfigBody(BaseModel):
    accessKeyId:     str
    secretAccessKey: str
    region:          str = "us-east-1"
    bucket:          str = "geobridge-data-backup"
    prefix:          str = "data/"


class AdminsBody(BaseModel):
    admins: list


@s3_router.post("/api/s3/test-connection")
async def s3_test_connection(body: S3ConfigBody):
    """Validate S3 credentials without saving them. Used by setup wizard."""
    if not _S3_AVAILABLE:
        raise HTTPException(503, "boto3 not installed")
    result = _s3.test_connection(
        body.accessKeyId, body.secretAccessKey, body.region, body.bucket
    )
    return result


@s3_router.post("/api/s3/save-config")
async def s3_save_config(body: S3ConfigBody):
    """Save AWS credentials to AppData and trigger an initial pull."""
    if not _S3_AVAILABLE:
        raise HTTPException(503, "boto3 not installed")
    _s3.save_config(
        body.accessKeyId, body.secretAccessKey,
        body.region, body.bucket, body.prefix
    )
    # Pull all shared files immediately so the new machine is up to date
    results = _s3.pull_all(force=False)
    updated = [k for k, v in results.items() if v == "updated"]
    return {"ok": True, "pulled": len(updated), "details": results}


@s3_router.get("/api/s3/status")
async def s3_status():
    """Return current sync state for the sidebar badge."""
    if not _S3_AVAILABLE:
        return {"configured": False, "error": "boto3 not installed"}
    return _s3.get_sync_state()


@s3_router.get("/api/s3/check-configured")
async def s3_check_configured():
    """Return whether aws_config.json exists. Used by App.jsx on startup."""
    if not _S3_AVAILABLE:
        return {"configured": False, "boto3": False}
    return {"configured": _s3.is_configured(), "boto3": True}


# The remaining S3 endpoints require a valid session
s3_auth_router = _APIRouter(dependencies=[Depends(require_session)])


@s3_auth_router.post("/api/s3/pull")
async def s3_pull_all():
    """Force a full pull from S3 (admin action from Settings page)."""
    if not _S3_AVAILABLE:
        raise HTTPException(503, "boto3 not installed")
    results = _s3.pull_all(force=True)
    updated = [k for k, v in results.items() if v == "updated"]
    return {"ok": True, "updated": len(updated), "details": results}


@s3_auth_router.post("/api/s3/push")
async def s3_push_all():
    """Force a full push to S3 (admin backup from Settings page)."""
    if not _S3_AVAILABLE:
        raise HTTPException(503, "boto3 not installed")
    results = _s3.push_all()
    ok_count = sum(1 for v in results.values() if v == "pushed")
    return {"ok": True, "pushed": ok_count, "details": results}


@s3_auth_router.get("/api/s3/admins")
async def get_admins():
    """Return the current admin list plus which usernames are permanently
    protected (always-admin, never removable from the UI)."""
    if not _S3_AVAILABLE:
        raise HTTPException(503, "boto3 not installed")
    return {"admins": _s3.get_admins(), "protected": _s3.get_default_admins()}


@s3_auth_router.post("/api/s3/admins")
async def save_admins(body: AdminsBody):
    """Update the admin list in S3. Only callable by existing admins (enforced in frontend)."""
    if not _S3_AVAILABLE:
        raise HTTPException(503, "boto3 not installed")
    ok = _s3.save_admins_to_s3(body.admins)
    if ok:
        _s3.invalidate_admins_cache()
    return {"ok": ok}


@s3_auth_router.get("/api/s3/is-admin/{username}")
async def check_is_admin(username: str):
    """Check if a username has admin privileges."""
    if not _S3_AVAILABLE:
        return {"isAdmin": False}
    return {"isAdmin": _s3.is_admin(username)}
