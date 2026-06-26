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
import html as _html
import json
import os

router = APIRouter()

# SKU key exactly as stored in QB data files (QB item codes are truncated at import)
HAN_CS_CUST_SKU = "Service Fee (HANOVER-CS) Cust (Service Fee Geotab (GO) - Hanover Cost Share for C..."

# SKUs that are billed from an external platform (not MyAdmin).
# For these, the QB invoice quantity is always correct — MyAdmin will never have
# matching devices, so they must never appear as over-billed.  The quantity row
# is emitted as "match" with myAdminCount == qbQty so the UI shows it neutrally.
QB_AUTHORITATIVE_SKUS: frozenset = frozenset({
    "BlueArrow Fuel Service",
    # NOTE: "Geotab Service Fee (HANOVER)" is intentionally NOT listed here.
    # It is handled in two places:
    #   1. Per Hanover customer (billing_type=="Hanover"): hanoverConsolidated branch
    #      in the qty loop — devices counted into hanover_myadmin_total accumulator.
    #   2. Hanover Insurance Group master row (QB-only stub): uses hanover_myadmin_total
    #      vs QB invoice qty for a real cross-customer diff.
})

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

    Rules (mirrors _extract_parent):
      • Pipe-location suffix " | Location Name" is stripped first so that
        'College Internship Program | Berkeley' → 'college internship program'
        and all locations merge to the same parent key.
      • If the FIRST brace token is exactly "{Han-CS}" (case-insensitive),
        it is kept as part of the name — Han-CS customers are distinct
        entities from their non-Han-CS counterparts in QB.
        Any SUBSEQUENT brace tokens are sub-account qualifiers and are
        stripped.
          'ACES Controls LLC {Han-CS}'            → 'aces controls llc {han-cs}'
          'ACES Controls LLC {Han-CS} {Cameras}'  → 'aces controls llc {han-cs}'
      • For all other first brace tokens, strip from the first brace onward
        (original sub-account behaviour):
          'Hoopaugh Grading LLC {3rd Party}'      → 'hoopaugh grading llc'
    """
    s = (s or "").strip()
    # Strip pipe-location suffix before any other processing
    pipe_pos = s.find(" | ")
    if pipe_pos != -1:
        s = s[:pipe_pos].strip()
    first_open = s.find("{")
    if first_open == -1:
        return s.lower()
    first_close = s.find("}", first_open)
    first_token = s[first_open + 1 : first_close].strip() if first_close != -1 else ""
    if first_token.lower() == "han-cs":
        # Keep "{Han-CS}" in the key; strip any further brace suffixes
        base = s[:first_close + 1].strip()   # e.g. "ACES Controls LLC {Han-CS}"
        return base.lower()
    else:
        # Ordinary sub-account suffix — strip from first brace
        return s[:first_open].strip().lower()


def _normalize_loose(s: str) -> str:
    """Lossy normalisation for fuzzy cross-source name matching.

    Strips punctuation that commonly differs between MyAdmin and QB exports
    (commas, periods, apostrophes, extra whitespace) so that names like
    "Hoopaugh Grading LLC" and "Hoopaugh Grading, LLC" both collapse to
    "hoopaugh grading llc".

    Used exclusively for the Hanover/Han-CS billing-type QB-invoice fallback
    where we need to match MyAdmin parent names against QB invoice names.
    NOT used for primary lookups (those use _normalize).
    """
    import re
    # First, apply the same pipe-location and brace-stripping logic as
    # _normalize so we compare the same base name without any location
    # qualifier, sub-account suffix, or Han-CS qualifier.
    s = (s or "").strip()
    pipe_pos = s.find(" | ")
    if pipe_pos != -1:
        s = s[:pipe_pos].strip()
    first_open = s.find("{")
    if first_open != -1:
        first_close = s.find("}", first_open)
        first_token = s[first_open + 1 : first_close].strip() if first_close != -1 else ""
        if first_token.lower() == "han-cs":
            s = s[:first_close + 1].strip()
        else:
            s = s[:first_open].strip()
    # Remove punctuation that differs between exports: commas, periods, apostrophes,
    # hyphens-as-punctuation (keep alphanumeric and spaces).
    s = re.sub(r"[,.\\'\"()]", "", s)
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def _extract_parent(cname: str):
    """Extract the canonical parent name and sub-account tag from a MyAdmin
    company name.

    MyAdmin naming convention:
      • "Acme Corp"                              → parent='Acme Corp',                    sub=''
      • "Acme Corp | Dallas"                     → parent='Acme Corp',                    sub=''
      • "Acme Corp {3rd Party Devices}"          → parent='Acme Corp',                    sub='3rd Party Devices'
      • "ACES Controls LLC {Han-CS}"             → parent='ACES Controls LLC {Han-CS}',   sub=''
      • "ACES Controls LLC {Han-CS} {Cameras}"   → parent='ACES Controls LLC {Han-CS}',   sub='Cameras'

    Rule:
      1. Strip pipe-location suffix (" | Location") first — it is a display
         qualifier only; all locations share one parent and one QB invoice.
      2. Find the first "{...}" token in the (now stripped) name.
      3. If it is "{Han-CS}" (case-insensitive), include it in the parent
         name; look for a SECOND "{...}" token for the sub-account tag.
      4. Otherwise, everything from the first "{" onward is stripped from
         the parent name and the content of the first braces is the tag.

    Returns:
        (parent_name: str, sub_account_tag: str)
    """
    # Step 1: strip pipe-location suffix
    pipe_pos = cname.find(" | ")
    if pipe_pos != -1:
        cname = cname[:pipe_pos].strip()

    first_open  = cname.find("{")
    if first_open == -1:
        return cname, ""

    first_close = cname.find("}", first_open)
    if first_close == -1:
        # Malformed — treat whole name as parent
        return cname, ""

    first_token = cname[first_open + 1 : first_close].strip()

    if first_token.lower() == "han-cs":
        # "{Han-CS}" is part of the identity — include it in parent name.
        parent_name = cname[:first_close + 1].strip()
        # Look for a sub-account tag after the {Han-CS} token.
        rest       = cname[first_close + 1:].strip()
        sub_open   = rest.find("{")
        if sub_open != -1:
            sub_close = rest.find("}", sub_open)
            sub_tag   = rest[sub_open + 1 : sub_close].strip() if sub_close != -1 else ""
        else:
            sub_tag = ""
        return parent_name, sub_tag
    else:
        # Ordinary sub-account: strip from first brace; the token itself is the tag.
        parent_name = cname[:first_open].strip()
        return parent_name, first_token


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
    from geotab.customers import (
        _sync_cache, enrich_customer, qb_customers as _qb_customers,
        billing_type_overrides as _billing_type_overrides,
    )

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

    # (norm_customerName, skuKey) -> mirrorOf prefix string
    # When present the SKU's MyAdmin count is computed as the sum of all
    # myadmin_by_sku counts whose key starts with the mirrorOf value.
    # Example: Manage Services mirrorOf "Service Fee Geotab" sums
    #          Base + Pro + ProPlus + Suspend V2 device counts.
    mirror_index: Dict[tuple, str] = {
        (_normalize(o["customerName"]), o["skuKey"]): o["mirrorOf"]
        for o in overrides
        if o.get("mirrorOf")
    }

    # QB invoice quantities: (norm_customerName, skuKey) -> qbQty
    qb_qty_index: Dict[tuple, int] = {
        (_normalize(q["customerName"]), q["skuKey"]): int(q.get("qbQty") or 0)
        for q in qb_qtys
    }

    # Set of LOOSELY-normalised QB customer names that have a HANOVER or Han-CS
    # line on their invoice.  Using _normalize_loose (punctuation-stripped) allows
    # matching MyAdmin names against QB invoice names even when they differ in
    # commas, periods, or similar minor punctuation — the most common mismatch.
    _HANOVER_QB_SKU     = "Geotab Service Fee (HANOVER)"
    _HAN_CS_QB_SKU_PART = "HANOVER-CS"   # substring present in HAN_CS_CUST_SKU
    qb_hanover_names: set = {
        _normalize_loose(nc) for (nc, sk) in qb_qty_index
        if sk == _HANOVER_QB_SKU
    }
    qb_han_cs_names: set = {
        _normalize_loose(nc) for (nc, sk) in qb_qty_index
        if _HAN_CS_QB_SKU_PART in sk
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
        cname   = _html.unescape(company.get("name") or "").strip()
        if not cid:
            continue

        # Derive parent name / key using the Han-CS-aware extractor.
        parent_name, _sub_tag = _extract_parent(cname)
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
        cname   = _html.unescape(company.get("name") or "").strip()
        if not cid:
            continue

        # Derive parent name / key (same logic as first pass).
        parent_name, sub_account_tag = _extract_parent(cname)
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
                "locationNames":    set(),   # pipe-separated locations e.g. "Berkeley", "Dallas"
            }
        # Always record the sub-account cid and display name.
        company_map[parent_key]["subAccountIds"].add(cid)
        if cname != parent_name:
            company_map[parent_key]["subAccountNames"].add(cname)
        # Track pipe-location names (e.g. "Berkeley" from "College Internship Program | Berkeley")
        # cname is still the raw MyAdmin name here; _extract_parent already stripped
        # the pipe suffix to get parent_name but did not modify cname itself.
        _pipe_pos = cname.find(" | ")
        if _pipe_pos != -1:
            _location = cname[_pipe_pos + 3:].strip()
            if _location:
                company_map[parent_key]["locationNames"].add(_location)
        # Prefer the parent name (no braces) as the display name.
        if not company_map[parent_key]["customerName"] and parent_name:
            company_map[parent_key]["customerName"] = parent_name

        # Extract pipe-location from raw cname for per-device location tracking
        _dev_pipe = cname.find(" | ")
        _dev_location = cname[_dev_pipe + 3:].strip() if _dev_pipe != -1 else ""
        company_map[parent_key]["devices"].append({
            "serialNumber":    serial,
            "promoCode":       promo_code,
            "billingPlan":     billing_plan,
            "neverActivated":  is_never_activated,
            # sub_account_tag from _extract_parent:
            #   - "ACES Controls LLC {Han-CS} {Cameras}" → "Cameras"
            #   - "Acme Corp {3rd Party Devices}"        → "3rd Party Devices"
            #   - "ACES Controls LLC {Han-CS}"           → ""  (tag is empty; Han-CS is the parent)
            # Tier 5 uses this to route {Han-CS} sub-account devices to HAN_CS_CUST_SKU.
            # For top-level Han-CS customers (billing_type == 'Han-CS'), Tier 5 fires
            # directly on billing_type — sub_account_tag will be empty here.
            "subAccountTag":   sub_account_tag,
            "location":        _dev_location,   # e.g. "Charlotte", "" for non-pipe accounts
        })

    # -- Reverse QB Han-CS check -----------------------------------------------
    # Find QB customers that have a HANOVER-CS SKU on their invoice but whose
    # name does NOT match any MyAdmin {Han-CS} customer (neither by {Han-CS} tag
    # nor by QB-fallback promotion).  These are either:
    #   a) Former Han-CS members whose MyAdmin account was renamed/removed, OR
    #   b) Name mismatches that prevent the QB-fallback from matching them.
    # Exclude Hanover Insurance Group (the master consolidated row).
    #
    # IMPORTANT: QB invoice names do NOT carry the {Han-CS} suffix — they use
    # the bare company name (e.g. "ACES Controls LLC", not "ACES Controls LLC {Han-CS}").
    # So when building the MyAdmin Han-CS name set we must strip "{Han-CS}" before
    # normalizing, otherwise nothing ever matches.
    def _strip_han_cs_tag(name: str) -> str:
        """Strip ' {Han-CS}' suffix (case-insensitive) from a customer name."""
        import re as _re
        return _re.sub(r'\s*\{Han-CS\}\s*', '', name, flags=_re.IGNORECASE).strip()

    # IMPORTANT: qb_qty_index keys use _normalize() (strict), so we must also
    # use _normalize() here — not _normalize_loose() — so the set membership
    # check (_nc not in _myadmin_han_cs_loose) compares like with like.
    # _strip_han_cs_tag removes " {Han-CS}" before normalizing so that
    # MyAdmin "ACES Controls LLC {Han-CS}" → "aces controls llc" matches
    # QB "aces controls llc" (already _normalize()'d in qb_qty_index).
    _myadmin_han_cs_loose: set = {
        _normalize(_strip_han_cs_tag(cdata["customerName"]))
        for cdata in company_map.values()
        if "{Han-CS}" in cdata["customerName"]
           or _normalize_loose(cdata["customerName"]) in qb_han_cs_names
    }
    _HIG_LOOSE = _normalize_loose("Hanover Insurance Group")
    _qb_han_cs_unmatched: list = []
    for _nc, _sk in qb_qty_index:
        if (_HAN_CS_QB_SKU_PART in _sk
                and _nc not in _myadmin_han_cs_loose
                and _nc != _HIG_LOOSE):
            # Look up display name and QB qty
            _qb_rec   = _qb_customers.get(_nc) or {}
            _disp     = _qb_rec.get("name") or _nc.title()
            _qb_qty   = qb_qty_index.get((_nc, _sk), 0)
            _qb_han_cs_unmatched.append({
                "customerName": _disp,
                "skuKey":       _sk,
                "qbQty":        _qb_qty,
                "note": (
                    "Has a HANOVER-CS SKU on QB invoice but no matching "
                    "{Han-CS} account found in MyAdmin. Check if the MyAdmin "
                    "account was renamed, removed, or needs a "
                    "billing_type_overrides.json entry."
                ),
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

    # Accumulate MyAdmin device count for "Geotab Service Fee (HANOVER)" across
    # all billing_type == "Hanover" customers.  These are invoiced consolidated
    # under Hanover Insurance Group in QB, not on each customer's own invoice.
    hanover_myadmin_total: int = 0

    # Accumulate MyAdmin device count for the HANOVER-CS Cust SKU across all
    # billing_type == "Han-CS" customers.  These are also invoiced under HIG in
    # QB (Service Fee (HANOVER-CS) line), not on each individual customer's invoice.
    han_cs_myadmin_total: int = 0

    # Customers assigned Han-CS via QB invoice fallback (no {Han-CS} in MyAdmin name).
    # Surfaced in the UI as an amber warning so ops can review / add overrides.
    _qb_fallback_customers: list = []

    for _pkey, cdata in company_map.items():
        cid          = cdata["customerId"]
        cname        = cdata["customerName"]
        devices      = cdata["devices"]
        # Re-scan all sub-account cids so billing type is found even when
        # the first cid seen belongs to a sub-account with an Unknown type.
        # Priority order (most specific wins):
        #   CUA / Hanover / Han-CS / Sourcewell / … > Standard > Unknown > (missing)
        _LOW_PRIORITY = {"Unknown", "Standard"}
        sub_cids     = cdata.get("subAccountIds") or {cid}
        billing_type = cdata.get("billingType") or "Standard"
        for _c in sub_cids:
            _bt = billing_type_index.get(_c)
            if _bt and _bt not in _LOW_PRIORITY:
                billing_type = _bt
                break
            if _bt and billing_type in _LOW_PRIORITY:
                billing_type = _bt   # take any definite value over the default

        # Manual billing-type overrides take highest priority — they correct
        # former Han-CS / Hanover customers that have left the program but
        # still appear on old QB invoices (which would trigger the QB fallback).
        _bt_override = _billing_type_overrides.get(_normalize(cname))
        if _bt_override:
            billing_type = _bt_override

        # Fallback: if billing_type is still Unknown or Standard, check the QB
        # invoice index by customer name.  Many Hanover customers have a name
        # mismatch between MyAdmin and QB (e.g. case, punctuation, suffix
        # differences) that causes enrich_customer() to return "Unknown".
        # The QB invoice is ground truth: if the customer's loosely-normalised
        # name appears as the payer of a HANOVER SKU line, they are Hanover.
        # _normalize_loose strips commas/periods so "Hoopaugh Grading LLC" and
        # "Hoopaugh Grading, LLC" both become "hoopaugh grading llc".
        if billing_type in ("Unknown", "Standard"):
            _nc = _normalize_loose(cname)
            if _nc in qb_hanover_names:
                billing_type = "Hanover"
            elif _nc in qb_han_cs_names:
                billing_type = "Han-CS"
                # Record customers promoted to Han-CS via QB fallback for the UI amber warning.
                # These customers have no {Han-CS} tag in MyAdmin and may need a billing_type_overrides.json entry.
                if "{Han-CS}" not in cname:
                    _active_count = len([d for d in devices if not d.get("neverActivated")])
                    _qb_fallback_customers.append({
                        "customerName":    cname,
                        "activeDevices":   _active_count,
                        "note": (
                            "Assigned Han-CS via QB invoice history. "
                            "If this customer is no longer in the Hanover Cost Share program, "
                            "add them to billing_type_overrides.json with value 'Standard'."
                        ),
                    })

        # Han-CS and Hanover are treated identically to CUA for never-activated
        # devices: billing only starts once a device has an active billing plan.
        # Never-activated devices on these accounts are NOT under-billed — they
        # simply haven't been deployed yet.
        is_cua       = billing_type in ("CUA", "Charge Upon Activation", "Hanover", "Han-CS")

        cust_ok = cust_over = cust_under = cust_unmapped = cust_no_price = 0
        cust_never_activated = 0
        cust_expected = cust_actual = 0.0
        device_rows = []

        # Per-customer HANOVER+GO-gated device counts (mirror the global
        # hanover_myadmin_total / han_cs_myadmin_total accumulators but scoped
        # to this customer so hanoverConsolidated qty rows show the correct
        # business-logic count rather than the raw myadmin_by_sku count).
        cust_han_cs_count:  int = 0
        cust_hanover_count: int = 0

        # Separate active from never-activated devices so we can inherit SKUs
        active_devices       = [d for d in devices if not d.get("neverActivated")]
        never_activated_devs = [d for d in devices if d.get("neverActivated")]

        # CUA / Hanover / Han-CS customers: never-activated devices are not billed — skip entirely.
        # Standard customers: process active devices first, then inherit SKU for
        # never-activated devices from the most common active SKU on the account.
        devices_to_process = active_devices if is_cua else devices

        # Track SKU usage for active devices so never-activated can inherit
        active_sku_counts: Dict[str, int] = {}

        # QB invoice data (ovr_index, qb_qty_index) is keyed by the bare company
        # name — QB never carries the MyAdmin "{Han-CS}" suffix.  Strip it before
        # normalizing so Han-CS customers match their QB invoice lines correctly.
        _qb_cname = _normalize(_strip_han_cs_tag(cname))

        for dev in devices_to_process:
            promo_code      = dev["promoCode"]       # e.g. "SWELL", "" (most devices)
            billing_plan    = dev["billingPlan"]     # e.g. "ProPlus Mode", "Base Mode" (suffix already stripped)
            serial          = dev.get("serialNumber") or ""
            norm_cname      = _qb_cname
            never_activated = dev.get("neverActivated", False)
            sub_account_tag = dev.get("subAccountTag") or ""  # e.g. "Han-CS", "3rd Party Devices"
            dev_location    = dev.get("location") or ""       # e.g. "Charlotte", "" for non-pipe

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

            # -- Tier 0.5a: {Cameras} sub-account tag = SS Service Fee -----------
            # Surfsight camera devices live under a "{Cameras}" sub-account on
            # Han-CS (and potentially other) parent accounts.  MyAdmin reports
            # their activeDevicePlan.name as "Pro Mode" — the same plan used by
            # standard Geotab GO devices — so the normal billing-plan lookup
            # would incorrectly route them to "Service Fee Geotab (Pro)".
            #
            # The sub_account_tag "Cameras" is the reliable discriminator for
            # Surfsight (AI-12/AI-14) cameras.  However, GO Focus Plus cameras
            # (serial prefix "GE" or "GF") can also live on a {Cameras}
            # sub-account and must NOT be routed here — they resolve correctly
            # via their promoCode (GFP-BUNDLE → "Geotab Service (GO Focus Plus)")
            # through the normal Tier 1–4 lookup chain.
            serial_upper = serial.upper()
            _is_ge_gf = serial_upper.startswith("GE") or serial_upper.startswith("GF")
            if sub_account_tag.lower() == "cameras" and not _is_ge_gf:
                sku_key      = "SS Service Fee"
                mapping_tier = "sub_account_tag"
                lookup_code  = "Cameras sub-account"

            # -- Tier 0.5b: HN serial prefix + Pro Mode = DM Service Fee -------
            # Devices with serial numbers starting with "HN" on Pro Mode billing
            # are DM (Data Management) units. They don't receive the CELU-TP-250
            # promo code in MyAdmin but belong to the same DM Service Fee family.
            # HN serials always correspond to the "(Hardwire)" QB SKU variant;
            # this flag is passed through to Tier 4.5 to select the right variant.
            is_hn_serial = serial_upper.startswith("HN")
            if (sku_key is None
                    and not promo_code
                    and is_hn_serial
                    and billing_plan.upper() == "PRO MODE"):
                sku_key      = "DM Service Fee"
                mapping_tier = "serial_prefix"
                lookup_code  = "HN serial + Pro Mode"
            else:
                is_hn_serial = False   # only relevant when we actually triggered Tier 0.5b

            # -- Tier 0.5c: EG / EK serial prefix = Phillips Connect Tracking Fee --
            # Phillips Connect devices carry serial numbers starting with "EG" or "EK".
            # MyAdmin reports their activeDevicePlan as "Pro Mode" — identical to
            # standard Geotab GO devices — so the normal billing-plan lookup would
            # incorrectly route them to "Service Fee Geotab (Pro)".
            # The serial prefix is the only reliable discriminator.
            if (sku_key is None
                    and (serial_upper.startswith("EG") or serial_upper.startswith("EK"))):
                sku_key      = "Tracking Fee"
                mapping_tier = "serial_prefix"
                lookup_code  = "EG/EK serial prefix (Phillips Connect)"

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
            # Devices map to the bare "DM Service Fee" anchor via promo code
            # (CELU-TP-250 / CELU-TP-200) or HN serial prefix (Tier 0.5).
            # QB invoices customers at a specific variant — two naming families:
            #   • "DM Service Fee (Periodic / Movement / Hardwire / Enterprise)"
            #   • "DM Service (Hardwire / Movement / Periodic / Barra) - RS"
            # Both families share the "DM Service" prefix.
            #
            # Rule: if resolved skuKey is "DM Service Fee" (bare anchor), scan
            # the customer's ovr_index AND qb_qty_index for any key that starts
            # with "DM Service" (covers both families) and is not the bare anchor
            # itself.  Pick the variant with the highest QB invoice quantity.
            if sku_key == "DM Service Fee":
                dm_variants = list({
                    sk
                    for (nc, sk) in list(ovr_index.keys()) + list(qb_qty_index.keys())
                    if nc == norm_cname
                    and sk.startswith("DM Service")
                    and sk != "DM Service Fee"
                })
                if dm_variants:
                    if is_hn_serial:
                        # HN serial → must be a Hardwire device; pick the variant
                        # whose name contains "(Hardwire)" (case-insensitive).
                        # Fall back to highest-qty variant if no Hardwire entry found.
                        hardwire = [sk for sk in dm_variants if "hardwire" in sk.lower()]
                        chosen   = hardwire[0] if hardwire else None
                        if not chosen:
                            dm_variants.sort(
                                key=lambda sk: qb_qty_index.get((norm_cname, sk), 0),
                                reverse=True,
                            )
                            chosen = dm_variants[0]
                    else:
                        # Non-HN serial (CELU-TP promo code path) → prefer the
                        # variant with the highest QB quantity that is NOT Hardwire,
                        # since HN devices already claim the Hardwire slot.
                        # Fall back to highest-qty overall if nothing else exists.
                        non_hw = [sk for sk in dm_variants if "hardwire" not in sk.lower()]
                        pool   = non_hw if non_hw else dm_variants
                        pool.sort(
                            key=lambda sk: qb_qty_index.get((norm_cname, sk), 0),
                            reverse=True,
                        )
                        chosen = pool[0]
                    sku_key = chosen
            # -----------------------------------------------------------------

            # -- Tier 4.6: Suspend family upgrade ------------------------------
            # The global SKU mapping resolves "SUSPEND MODE" to the bare anchor
            # "Service Fee Geotab (Suspend)".  Some customers are invoiced in QB
            # on "Service Fee Geotab (Suspend V2)" instead — both SKUs represent
            # the same MyAdmin billing plan; which one applies depends solely on
            # what is set on the customer's QB invoice.
            #
            # Rule: if resolved skuKey is the bare Suspend anchor, scan
            # ovr_index and qb_qty_index for any "Service Fee Geotab (Suspend"
            # variant that is NOT the bare anchor and upgrade to it.
            # If multiple variants somehow exist, pick the one with the highest
            # QB invoice quantity.
            if sku_key == "Service Fee Geotab (Suspend)":
                suspend_variants = list({
                    sk
                    for (nc, sk) in list(ovr_index.keys()) + list(qb_qty_index.keys())
                    if nc == norm_cname
                    and sk.startswith("Service Fee Geotab (Suspend")
                    and sk != "Service Fee Geotab (Suspend)"
                })
                if suspend_variants:
                    suspend_variants.sort(
                        key=lambda sk: qb_qty_index.get((norm_cname, sk), 0),
                        reverse=True,
                    )
                    sku_key = suspend_variants[0]
            # -----------------------------------------------------------------

            # -- Tier 5: Han-CS billing-type override -------------------------
            # "Han-CS" = Hanover Cost Share: the customer is subsidised by
            # Hanover Insurance and is invoiced on the HANOVER-CS Cust SKU
            # regardless of what MyAdmin reports as the billing plan.
            #
            # MyAdmin stores Han-CS customers with "{Han-CS}" as part of the
            # company name (e.g. "ACES Controls LLC {Han-CS}").  _extract_parent
            # treats "{Han-CS}" as part of the parent name, so these customers
            # appear as standalone entries with billing_type == "Han-CS".
            #
            # EXCEPTION: sub-accounts with a non-Han-CS tag (e.g. "{Cameras}",
            # "{3rd Party Devices}") are billed on their own SKU (e.g. SS Service
            # Fee for Surfsight cameras), NOT on the HANOVER-CS Cust SKU.
            # These devices have sub_account_tag set to "Cameras" etc. and must
            # be routed by their actual billing plan, not force-overridden here.
            # Only apply the Han-CS override when:
            #   a) the device has no sub-account tag (it's directly on the Han-CS
            #      parent account), OR
            #   b) the sub-account tag is explicitly "han-cs" (legacy safety net).
            #
            # "Hanover" (without cost-share) must NOT be overridden here.
            _is_han_cs_sub = sub_account_tag.lower() == "han-cs"
            _is_other_sub  = sub_account_tag and not _is_han_cs_sub
            if (billing_type == "Han-CS" or _is_han_cs_sub) and not _is_other_sub:
                sku_key      = HAN_CS_CUST_SKU
                mapping_tier = "billing_type"
                lookup_code  = f"Han-CS override ({billing_plan or promo_code or 'none'})"
            # -----------------------------------------------------------------

            # Track active SKU usage for never-activated inheritance
            if sku_key:
                active_sku_counts[sku_key] = active_sku_counts.get(sku_key, 0) + 1

            # Per-device promoCode-gated accumulation for the HIG master row.
            # Billing rules (per business logic):
            #   • promoCode must be "HANOVER" (uppercased on read, so case-insensitive)
            #   • device must be active (not never_activated)
            #   • billingPlan must be strictly the base "GO" plan — suspended, pro,
            #     proplus, GO Expand, etc. are NOT counted because we only bill
            #     Han-CS / Hanover customers when the device is on an active GO plan.
            #
            # Han-CS split: a device is Han-CS if its account is Han-CS
            # (billing_type == "Han-CS") OR the device came from a {Han-CS}
            # sub-account (sub_account_tag.lower() == "han-cs").
            # All other qualifying devices on non-Han-CS accounts go to the
            # standard "Geotab Service Fee (HANOVER)" bucket.
            if (promo_code == "HANOVER"
                    and not never_activated
                    and billing_plan.upper() == "GO"):
                _is_han_cs_device = (
                    billing_type == "Han-CS"
                    or sub_account_tag.lower() == "han-cs"
                )
                if _is_han_cs_device:
                    han_cs_myadmin_total += 1
                    cust_han_cs_count    += 1
                else:
                    hanover_myadmin_total += 1
                    cust_hanover_count   += 1
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
                    "location":      dev_location,
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
                    "location":      dev_location,
                })
                cust_unmapped += 1
                continue

            rate_plan = lookup_code  # used by the rest of the loop for display

            # Resolve expected price
            expected, price_source = _resolve_price(_qb_cname, sku_key, ovr_index, catalog_index)

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
                    "location":      dev_location,
                })
                cust_no_price += 1
                continue

            # Resolve actual QB invoiced price (customer override is the invoice truth)
            actual_key = (_qb_cname, sku_key)
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
                    "location":      dev_location,
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
            "location":      dev_location,
            })

        # -- Never-activated devices (Standard customers only) -----------------
        # CUA customers: already excluded from devices_to_process above.
        # Standard customers: never-activated devices are billed at the same SKU
        # as the other active devices on the account.  Inherit the most-used
        # active SKU.  If no active devices exist yet (brand-new account), we
        # emit them as "unmapped" so they show up visibly.
        #
        # Exception: never-activated devices whose promoCode is "HANOVER" are
        # billed via the Hanover Insurance Group master invoice — and only when
        # active on the base GO plan.  A never-activated HANOVER device has
        # never been deployed, so HIG has never billed for it and neither should
        # we.  Exclude these regardless of the account's billing_type label:
        # some Hanover sub-customers are resolved as "Standard" in our system
        # (because their QB invoice carries no HANOVER SKU line), but the
        # promoCode is the authoritative signal that the device is HIG-covered.
        # Count of never-activated HANOVER devices on Standard-labelled accounts.
        # These are excluded from never-activated processing below and must also
        # be subtracted from cust_myadmin_direct (line below).
        _hanover_na_excluded = 0
        if not is_cua and never_activated_devs:
            # Pick the most common active SKU (or "" if no active devices)
            inherited_sku = (
                max(active_sku_counts, key=active_sku_counts.get)
                if active_sku_counts else ""
            )
            for na_dev in never_activated_devs:
                # Skip never-activated HANOVER promoCode devices — they are
                # billed via Hanover Insurance Group only when active on GO
                # plan; a never-activated device is not billed by anyone.
                if (na_dev.get("promoCode") or "").upper() == "HANOVER":
                    _hanover_na_excluded += 1
                    continue
                if inherited_sku:
                    expected_na, price_source_na = _resolve_price(
                        _qb_cname, inherited_sku, ovr_index, catalog_index
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
                    "location":      na_dev.get("location", ""),
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
                    "location":      na_dev.get("location", ""),
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
        # Also track how many of the never-activated devices landed on each SKU
        # so qty_rows can show per-SKU never-activated counts instead of the
        # customer-level total on every row.
        never_activated_by_sku: Dict[str, int] = {}
        for row in device_rows:
            sk = row.get("skuKey") or ""
            if sk:
                myadmin_by_sku[sk] = myadmin_by_sku.get(sk, 0) + 1
                if row.get("neverActivated"):
                    never_activated_by_sku[sk] = never_activated_by_sku.get(sk, 0) + 1

        # hanover_myadmin_total and han_cs_myadmin_total are incremented
        # per-device inside the device loop above (promoCode == "HANOVER" gate).
        # No post-loop accumulation needed here.

        qty_rows = []
        cust_qty_match = cust_qty_over = cust_qty_under = cust_qty_missing = 0

        # All SKUs seen either in MyAdmin mapping or in QB invoice for this customer.
        # Use _qb_cname (Han-CS suffix stripped) so QB lines keyed to the bare name
        # are included even when MyAdmin carries the "{Han-CS}" suffix.
        all_skus = set(myadmin_by_sku.keys()) | {
            sk for (nc, sk) in qb_qty_index.keys() if nc == _qb_cname
        }

        # Count unmapped devices (no rate plan OR no mapping, excl. never_activated)
        cust_unmapped_count = sum(
            1 for row in device_rows
            if row.get("status") == "unmapped" and not row.get("neverActivated")
        )

        for sku_key in sorted(all_skus):
            # Mirror SKUs: MyAdmin count = sum of all SKUs whose key starts with
            # the mirrorOf prefix for this customer.  This lets an add-on QB line
            # (e.g. "Manage Services" @ $3/device) track the total device count
            # of a family of service SKUs (e.g. all "Service Fee Geotab (*)"
            # variants) without those devices being double-counted in the header.
            _mirror_prefix = mirror_index.get((_qb_cname, sku_key))
            if _mirror_prefix:
                myadmin_count = sum(
                    cnt for sk, cnt in myadmin_by_sku.items()
                    if sk.startswith(_mirror_prefix)
                )
            else:
                myadmin_count = myadmin_by_sku.get(sku_key, 0)
            qb_qty        = qb_qty_index.get((_qb_cname, sku_key), None)

            # Hanover-consolidated: "Geotab Service Fee (HANOVER)" and HAN_CS_CUST_SKU
            # are both invoiced under Hanover Insurance Group's QB master account —
            # never on each individual customer's own QB invoice.
            # Detect by SKU key directly (promoCode is the ground truth) rather than
            # by billing_type label, which may be Unknown due to name mismatches.
            # Show as hanoverConsolidated=True so the UI annotates with "via Hanover Ins."
            # and devices don't appear as missing-from-QB on the individual row.
            _is_hanover_sku = (sku_key == "Geotab Service Fee (HANOVER)")
            _is_han_cs_sku  = (sku_key == HAN_CS_CUST_SKU)
            if _is_hanover_sku or _is_han_cs_sku:
                # These SKUs are invoiced under Hanover Insurance Group's QB
                # master account.  Whether or not the individual customer's QB
                # export also carries a line for them, we always treat them as
                # HIG-consolidated: use the gated MyAdmin count and mark
                # qbAuthoritative=True so the row is excluded from cust_qb_total
                # and the header delta.
                _gated_count = cust_han_cs_count if _is_han_cs_sku else cust_hanover_count
                qty_rows.append({
                    "skuKey":              sku_key,
                    "myAdminCount":        _gated_count,
                    "qbQty":               None,
                    "qtyDelta":            0,
                    "qtyStatus":           "match",
                    "unmappedCount":       cust_unmapped_count,
                    "neverActivatedCount": never_activated_by_sku.get(sku_key, 0),
                    "qbAuthoritative":     True,
                    "hanoverConsolidated": True,
                })
                cust_qty_match += 1
                continue

            # QB-authoritative SKUs (e.g. BlueArrow Fuel Service) are billed from
            # an external platform — MyAdmin will never have devices for them.
            # Treat QB qty as ground truth: show as "match" so they never appear
            # as over-billed, and exclude them from the customer delta totals.
            # Also covers the Hanover Insurance Group master account row which holds
            # the aggregated Geotab Service Fee (HANOVER) QB qty for all sub-customers.
            if sku_key in QB_AUTHORITATIVE_SKUS:
                effective_myadmin = qb_qty if qb_qty is not None else 0
                qty_rows.append({
                    "skuKey":              sku_key,
                    "myAdminCount":        effective_myadmin,
                    "qbQty":               qb_qty,
                    "qtyDelta":            0,
                    "qtyStatus":           "match",
                    "unmappedCount":       cust_unmapped_count,
                    "neverActivatedCount": never_activated_by_sku.get(sku_key, 0),
                    "qbAuthoritative":     True,
                })
                cust_qty_match += 1
                continue

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
                "neverActivatedCount": never_activated_by_sku.get(sku_key, 0),
            })

        cust_myadmin_total = len(devices)

        # CUA (Charge Upon Activation) customers: never-activated devices are
        # not billed and are excluded from the QB invoice.  Subtract them from
        # the MyAdmin total so the header delta only reflects billed devices.
        _cua_never_activated = len(never_activated_devs) if is_cua else 0
        # Also subtract HANOVER never-activated devices excluded above on Standard accounts.
        _cua_never_activated += _hanover_na_excluded

        # Hanover-consolidated devices (HANOVER+GO-gated) are billed via the
        # Hanover Insurance Group master invoice — not on this customer's own QB
        # invoice.  Exclude them from the customer-level MyAdmin total so the
        # header delta only reflects devices the customer is directly billed for.
        #
        # cust_hanover_count  — Hanover (non-Han-CS) devices that passed the gate
        # cust_han_cs_count   — Han-CS devices that passed the gate
        #
        # Note: Han-CS customers also have their HANOVER+GO-gated devices covered
        # by HIG (on the HANOVER-CS line), so both counts are excluded here.
        # Non-gated devices on a Han-CS account (e.g. suspended, ProPlus) still
        # route to HAN_CS_CUST_SKU but are NOT covered by HIG — they remain in
        # cust_myadmin_direct so any billing gap is still visible.
        _hig_covered        = cust_hanover_count + cust_han_cs_count
        cust_myadmin_direct = cust_myadmin_total - _hig_covered - _cua_never_activated

        # Exclude QB-authoritative SKUs (e.g. BlueArrow Fuel Service) from the
        # customer-level QB total and delta — those SKUs are always in balance by
        # definition and should not skew the MyAdmin-vs-QB header numbers.
        cust_qb_total = sum(
            r["qbQty"] for r in qty_rows
            if r["qbQty"] is not None and not r.get("qbAuthoritative")
        )
        has_qb_data   = any(r["qbQty"] is not None for r in qty_rows)

        # Special case: a customer whose ONLY billable devices are HIG-consolidated
        # (all HANOVER+GO-gated, nothing on a direct QB invoice) will have
        # has_qb_data=False because hanoverConsolidated rows carry qbQty=None.
        # But those devices ARE accounted for — on the HIG master invoice.
        # Treat them as matched rather than "No QB Data".
        if not has_qb_data and cust_myadmin_direct == 0 and _hig_covered > 0:
            has_qb_data = True

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
        total_myadmin_devices += cust_myadmin_direct
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
            "locationNames":     sorted(cdata.get("locationNames") or []),
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
            # Quantity reconciliation fields.
            # myAdminTotal excludes HIG-covered (HANOVER+GO-gated) devices so the
            # delta only reflects devices billed directly on this customer's invoice.
            "myAdminTotal":      cust_myadmin_direct,
            "qbTotal":           cust_qb_total if has_qb_data else None,
            "qtyDelta":          (cust_myadmin_direct - cust_qb_total) if has_qb_data else None,
            "qtyMatch":          cust_qty_match,
            "qtyUnderBilled":    cust_qty_under,
            "qtyOverBilled":     cust_qty_over,
            "qtyMissing":        cust_qty_missing,
            "hasQbData":         has_qb_data,
            "skuQtyBreakdown":   qty_rows,
        })

    # -- QB-only customers -------------------------------------------------------
    # Some QB customers (e.g. Hanover Insurance Group) are pure billing accounts
    # with no MyAdmin presence.  They have entries in qb_qty_index but were never
    # added to company_map.  Inject them as stub rows so their QB qty is visible
    # in reconciliation and they are not silently ignored.
    #
    # Only inject when not filtering by a specific customer_id (the QB-only
    # account won't have a matching MyAdmin cid anyway).
    if not customer_id:
        # Collect the set of normalised names already covered by company_map
        covered_norm_names: set = {
            _normalize(_strip_han_cs_tag(c["customerName"])) for c in result_customers
        }

        # Group qb_qty_index keys by customer name
        qb_only_names: Dict[str, list] = {}
        for (nc, sk), qty in qb_qty_index.items():
            if nc not in covered_norm_names:
                qb_only_names.setdefault(nc, []).append((sk, qty))

        print(f"[reconciliation] QB-only injection: {len(qb_only_names)} customers "
              f"(in qb_qty_index but not in MyAdmin company_map); "
              f"hanover_myadmin_total={hanover_myadmin_total}  "
              f"han_cs_myadmin_total={han_cs_myadmin_total}")

        for norm_nc, sku_entries in sorted(qb_only_names.items()):
            # Look up display name and billing type from qb_customers
            qb_rec       = _qb_customers.get(norm_nc) or {}
            display_name = qb_rec.get("name") or norm_nc.title()
            bt           = qb_rec.get("billingType") or "Unknown"

            # Hanover Insurance Group: "Geotab Service Fee (HANOVER)" is the
            # consolidated QB line for ALL billing_type=="Hanover" customers.
            # Use the accumulated MyAdmin total so the diff is meaningful.
            is_hig = (norm_nc == _normalize("Hanover Insurance Group"))

            # QB-only rows always have status "ok" unless we can compute a real delta.
            # Compute status based on qty delta if we have a real MyAdmin total.
            stub_qty_rows  = []
            stub_qb_total  = 0
            stub_qty_match = stub_qty_under = stub_qty_over = stub_qty_missing = 0

            for sku_key, qb_qty in sorted(sku_entries):
                # For HIG's HANOVER SKU, use the accumulated cross-customer count
                if is_hig and sku_key == "Geotab Service Fee (HANOVER)":
                    my_count  = hanover_myadmin_total
                    qty_delta = my_count - qb_qty
                    if qty_delta == 0:
                        qty_status = "match"
                        stub_qty_match += 1
                    elif qty_delta > 0:
                        qty_status = "under_billed"   # more MyAdmin than QB invoiced
                        stub_qty_under += 1
                    else:
                        qty_status = "over_billed"    # fewer MyAdmin than QB invoiced
                        stub_qty_over += 1
                    stub_qty_rows.append({
                        "skuKey":              sku_key,
                        "myAdminCount":        my_count,
                        "qbQty":               qb_qty,
                        "qtyDelta":            qty_delta,
                        "qtyStatus":           qty_status,
                        "unmappedCount":       0,
                        "neverActivatedCount": 0,
                        "qbAuthoritative":     False,
                        "qbOnly":              True,
                        "hanoverMaster":       True,   # flag: this is the aggregated row
                    })
                    stub_qb_total += qb_qty

                # For HIG's HANOVER-CS SKU, use the accumulated Han-CS MyAdmin count.
                # Match by substring since the truncated SKU key may vary slightly.
                elif is_hig and _HAN_CS_QB_SKU_PART in sku_key:
                    my_count  = han_cs_myadmin_total
                    qty_delta = my_count - qb_qty
                    if qty_delta == 0:
                        qty_status = "match"
                        stub_qty_match += 1
                    elif qty_delta > 0:
                        qty_status = "under_billed"
                        stub_qty_under += 1
                    else:
                        qty_status = "over_billed"
                        stub_qty_over += 1
                    stub_qty_rows.append({
                        "skuKey":              sku_key,
                        "myAdminCount":        my_count,
                        "qbQty":               qb_qty,
                        "qtyDelta":            qty_delta,
                        "qtyStatus":           qty_status,
                        "unmappedCount":       0,
                        "neverActivatedCount": 0,
                        "qbAuthoritative":     False,
                        "qbOnly":              True,
                        "hanoverMaster":       True,
                    })
                    stub_qb_total += qb_qty
                else:
                    # All other QB-only SKUs: no MyAdmin equivalent, show as authoritative
                    stub_qty_rows.append({
                        "skuKey":              sku_key,
                        "myAdminCount":        qb_qty,   # mirror QB qty — authoritative
                        "qbQty":               qb_qty,
                        "qtyDelta":            0,
                        "qtyStatus":           "match",
                        "unmappedCount":       0,
                        "neverActivatedCount": 0,
                        "qbAuthoritative":     True,
                        "qbOnly":              True,
                    })
                    stub_qty_match += 1
                    stub_qb_total += qb_qty

            # Derive overall stub status from SKU-level results
            if stub_qty_under > 0:
                stub_status = "ok"   # under-billed means we have more devices than invoiced
            elif stub_qty_over > 0:
                stub_status = "ok"   # surface the discrepancy in qty breakdown
            else:
                stub_status = "ok"

            # Skip if status_filter wouldn't match
            if status_filter and status_filter != "ok":
                continue

            # HIG total = Hanover devices + Han-CS devices (both roll up to HIG in QB)
            hig_myadmin_total = hanover_myadmin_total + han_cs_myadmin_total

            result_customers.append({
                "customerId":       f"qb-only:{norm_nc}",
                "customerName":     display_name,
                "billingType":      bt,
                "subAccountNames":  [],
                "locationNames":    [],
                "deviceCount":      hig_myadmin_total if is_hig else 0,
                "ok":               0,
                "over":             0,
                "under":            0,
                "unmapped":         0,
                "neverActivated":   0,
                "noPrice":          0,
                "expectedMonthly":  0.0,
                "actualMonthly":    0.0,
                "delta":            0.0,
                "status":           stub_status,
                "devices":          [],
                "myAdminTotal":     hig_myadmin_total if is_hig else 0,
                "qbTotal":          stub_qb_total,
                "qtyDelta":         (hig_myadmin_total - stub_qb_total) if is_hig else 0,
                "qtyMatch":         stub_qty_match,
                "qtyUnderBilled":   stub_qty_under,
                "qtyOverBilled":    stub_qty_over,
                "qtyMissing":       stub_qty_missing,
                "hasQbData":        True,
                "skuQtyBreakdown":  stub_qty_rows,
                "qbOnly":           True,       # flag for frontend
            })
    # ---------------------------------------------------------------------------

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
        # Customers whose billing type was inferred from QB invoice history
        # (no {Han-CS} / {Hanover} tag in MyAdmin). If any of these are former
        # program members, add them to billing_type_overrides.json.
        "qbFallbackCustomers": _qb_fallback_customers,
        # QB customers with a HANOVER-CS SKU but no matching {Han-CS} MyAdmin account.
        # These need investigation — possible name mismatch or departed customer.
        "qbHanCsUnmatched": _qb_han_cs_unmatched,
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
