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
    from geotab.customers import _sync_cache, enrich_customer, qb_customers as _qb_customers

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
        cname   = _html.unescape(company.get("name") or "").strip()
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
        cname   = _html.unescape(company.get("name") or "").strip()
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
            # Preserve the sub-account tag (text inside braces, if any).
            # e.g. "ACES Controls LLC {Han-CS}" -> subAccountTag = "Han-CS"
            # Used in reconciliation to route {Han-CS} devices to the cost-share SKU
            # regardless of the parent account's billing type.
            "subAccountTag":   cname[brace+1:cname.rfind("}")].strip() if brace != -1 else "",
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
            serial          = dev.get("serialNumber") or ""
            norm_cname      = _normalize(cname)
            never_activated = dev.get("neverActivated", False)
            sub_account_tag = dev.get("subAccountTag") or ""  # e.g. "Han-CS", "3rd Party Devices"

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

            # -- Tier 0.5: HN serial prefix + Pro Mode = DM Service Fee -------
            # Devices with serial numbers starting with "HN" on Pro Mode billing
            # are DM (Data Management) units. They don't receive the CELU-TP-250
            # promo code in MyAdmin but belong to the same DM Service Fee family.
            # HN serials always correspond to the "(Hardwire)" QB SKU variant;
            # this flag is passed through to Tier 4.5 to select the right variant.
            is_hn_serial = serial.upper().startswith("HN")
            if (not promo_code
                    and is_hn_serial
                    and billing_plan.upper() == "PRO MODE"):
                sku_key      = "DM Service Fee"
                mapping_tier = "serial_prefix"
                lookup_code  = "HN serial + Pro Mode"
            else:
                is_hn_serial = False   # only relevant when we actually triggered Tier 0.5

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
            # Also fires when the device belongs to a "{Han-CS}" sub-account
            # under a Hanover parent (e.g. "ACES Controls LLC {Han-CS}").
            # The sub-account tag takes priority over the parent billing type.
            #
            # "Hanover" (without cost-share) must NOT be overridden here.
            if billing_type == "Han-CS" or sub_account_tag.lower() == "han-cs":
                sku_key      = HAN_CS_CUST_SKU
                mapping_tier = "billing_type"
                lookup_code  = f"Han-CS override ({billing_plan or promo_code or 'none'})"
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

        # Hanover customers: ALL GO devices roll up to the HIG master invoice,
        # regardless of which SKU they map to individually.  Count every device
        # that is NOT the Han-CS cost-share SKU toward the consolidated total.
        # Devices in a "{Han-CS}" sub-account are already routed to HAN_CS_CUST_SKU
        # by Tier 5, so they naturally fall into the exclusion below.
        if billing_type == "Hanover":
            for sk, cnt in myadmin_by_sku.items():
                if sk != HAN_CS_CUST_SKU:
                    hanover_myadmin_total += cnt

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

            # Hanover-consolidated SKU: "Geotab Service Fee (HANOVER)" is invoiced
            # under Hanover Insurance Group's QB account, not under each individual
            # Hanover customer.  For billing_type == "Hanover" customers, treat the
            # MyAdmin device count as authoritative — there will never be a matching
            # QB qty row on the customer's own invoice.
            # Hanover-consolidated: ALL SKUs on billing_type=="Hanover" customers
            # are invoiced under Hanover Insurance Group's master QB account.
            # Individual customer invoices never have QB qty rows for these devices.
            # Show as match with hanoverConsolidated=True so the UI annotates them
            # with "via Hanover Ins." and they don't show as missing from QB.
            if billing_type == "Hanover" and qb_qty is None and sku_key != HAN_CS_CUST_SKU:
                # Device count already accumulated into hanover_myadmin_total above.
                qty_rows.append({
                    "skuKey":              sku_key,
                    "myAdminCount":        myadmin_count,
                    "qbQty":               None,
                    "qtyDelta":            0,
                    "qtyStatus":           "match",
                    "unmappedCount":       cust_unmapped_count,
                    "neverActivatedCount": cust_never_activated,
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
                    "neverActivatedCount": cust_never_activated,
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
                "neverActivatedCount": cust_never_activated,
            })

        cust_myadmin_total = len(devices)
        # Exclude QB-authoritative SKUs (e.g. BlueArrow Fuel Service) from the
        # customer-level QB total and delta — those SKUs are always in balance by
        # definition and should not skew the MyAdmin-vs-QB header numbers.
        cust_qb_total = sum(
            r["qbQty"] for r in qty_rows
            if r["qbQty"] is not None and not r.get("qbAuthoritative")
        )
        has_qb_data   = any(r["qbQty"] is not None for r in qty_rows)

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
        covered_norm_names: set = {_normalize(c["customerName"]) for c in result_customers}

        # Group qb_qty_index keys by customer name
        qb_only_names: Dict[str, list] = {}
        for (nc, sk), qty in qb_qty_index.items():
            if nc not in covered_norm_names:
                qb_only_names.setdefault(nc, []).append((sk, qty))

        print(f"[reconciliation] QB-only injection: {len(qb_only_names)} customers "
              f"(in qb_qty_index but not in MyAdmin company_map); "
              f"hanover_myadmin_total={hanover_myadmin_total}")

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

            result_customers.append({
                "customerId":       f"qb-only:{norm_nc}",
                "customerName":     display_name,
                "billingType":      bt,
                "subAccountNames":  [],
                "deviceCount":      hanover_myadmin_total if is_hig else 0,
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
                "myAdminTotal":     hanover_myadmin_total if is_hig else 0,
                "qbTotal":          stub_qb_total,
                "qtyDelta":         (hanover_myadmin_total - stub_qb_total) if is_hig else 0,
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
