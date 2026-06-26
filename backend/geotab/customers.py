from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, List
import asyncio
import csv
import html as _html
import io
import json
import os
import sys
import time
from .auth import myadmin_call, session_store

# --- Windows-safe print (avoids CP1252 UnicodeEncodeError on arrow chars) -----
def _print(*args, **kwargs):
    """print() wrapper that replaces un-encodable chars instead of crashing."""
    msg = " ".join(str(a) for a in args)
    safe = msg.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
        sys.stdout.encoding or "utf-8", errors="replace"
    )
    print(safe, **kwargs)

router = APIRouter()

# --- CELU01 is the only MyAdmin account we pull device data for ---------------
MYADMIN_ACCOUNT = "CELU01"

# --- Disk persistence paths ---------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
QB_DATA_FILE           = os.path.join(_HERE, "qb_customers.json")
OVERRIDES_FILE         = os.path.join(_HERE, "billing_overrides.json")
BILLING_TYPE_OVERRIDES_FILE = os.path.join(_HERE, "billing_type_overrides.json")
BILLING_DATE_OVERRIDES_FILE = os.path.join(_HERE, "billing_date_overrides.json")
SYNC_CACHE_FILE        = os.path.join(_HERE, "myadmin_cache.json")   # persisted between restarts

def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def _save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

# --- In-memory stores -- pre-loaded from disk on startup ----------------------
billing_overrides:       Dict[str, str]  = _load_json(OVERRIDES_FILE, {})
billing_type_overrides:  Dict[str, str]  = {
    k: v for k, v in _load_json(BILLING_TYPE_OVERRIDES_FILE, {}).items()
    if not k.startswith("_")   # skip _comment keys
}
# Manual billing start date overrides: {"SERIAL": "YYYY-MM-DD"}
# Written by POST /api/customers/device/{serial}/billing-date
billing_date_overrides:  Dict[str, str]  = _load_json(BILLING_DATE_OVERRIDES_FILE, {})
qb_customers:       Dict[str, dict] = _load_json(QB_DATA_FILE, {})
qb_items:           List[dict]      = []
name_to_company_id: Dict[str, str]  = {}   # normalize(name) -> companyId, built on sync

# --- MyAdmin sync cache -------------------------------------------------------
CACHE_TTL_HOURS = 3              # Contracts auto-expire after 3 h; background task refreshes silently
DEVICE_DB_REFRESH_MINUTES = 30   # Background Step-1 (device DBs only) refresh interval
_sync_cache: Dict = _load_json(SYNC_CACHE_FILE, {})

# --- Sync lock -- prevents concurrent fetches when multiple requests arrive ---
_sync_lock = asyncio.Lock()

_qb_loaded = bool(qb_customers)
print(f"[customers] QB data: {len(qb_customers)} customers loaded from disk"
      if _qb_loaded else "[customers] QB data: no saved file -- import a CSV to populate")
if _sync_cache.get("fetched_at"):
    age_h = (time.time() - _sync_cache["fetched_at"]) / 3600
    print(f"[customers] MyAdmin cache: {len(_sync_cache.get('contracts', []))} contracts, "
          f"{age_h:.1f}h old (TTL {CACHE_TTL_HOURS}h)")

# --- Real-time sync progress state --------------------------------------------
# Updated by _fetch_myadmin_customers(); polled by the SSE endpoint.
_sync_progress: Dict = {
    "active":           False,   # True while a sync is running
    "step":             "",      # "step1" | "step2" | "processing" | "done" | "error"
    "step_label":       "",      # Human-readable current phase
    "page":             0,       # Current page number (step 2)
    "total_pages_est":  0,       # Estimated total pages (step 2)
    "records":          0,       # Records fetched so far (step 2)
    "pct":              0,       # 0-100 overall percentage
    "message":          "",      # Short status line shown under the bar
    "error":            "",      # Error message if step == "error"
}

def _set_progress(**kwargs):
    """Merge kwargs into _sync_progress in-place (thread-safe for our single-worker use)."""
    _sync_progress.update(kwargs)


# --- Background auto-refresh --------------------------------------------------
# Every DEVICE_DB_REFRESH_MINUTES: silently refresh Step 1 (device DBs).
# Every CACHE_TTL_HOURS: silently refresh Step 1 + Step 2 (full sync).
# Both skip if no session is active or a manual sync is already running.

_bg_refresh_task: asyncio.Task | None = None


async def _background_device_db_refresh() -> None:
    """Infinite loop: refresh device DBs every 30 min; full sync every 4 h."""
    step1_interval     = DEVICE_DB_REFRESH_MINUTES * 60
    full_sync_interval = CACHE_TTL_HOURS * 3600
    last_full_sync     = time.time()   # treat startup as a full sync baseline

    await asyncio.sleep(step1_interval)   # first run after one step-1 interval

    while True:
        try:
            if session_store.get("session_id") and not _sync_progress.get("active"):
                now           = time.time()
                do_full_sync  = (now - last_full_sync) >= full_sync_interval

                if do_full_sync:
                    # ── Full background sync (Step 1 + Step 2) ──────────────
                    print(f"[bg-refresh] Running silent full sync "
                          f"(TTL {CACHE_TTL_HOURS}h reached)...")
                    try:
                        await _fetch_myadmin_customers(force_refresh=True)
                        last_full_sync = time.time()
                        print("[bg-refresh] Silent full sync complete.")
                    except Exception as exc:
                        print(f"[bg-refresh] Silent full sync failed (non-fatal): {exc}")

                else:
                    # ── Step-1 only (device DBs) ────────────────────────────
                    print(f"[bg-refresh] Running silent Step-1 refresh "
                          f"(every {DEVICE_DB_REFRESH_MINUTES} min)...")
                    next_id = 0
                    page_num = 0
                    all_device_dbs = []
                    while True:
                        page_num += 1
                        result = await myadmin_call(
                            "GetCurrentDeviceDatabases",
                            {
                                "apiKey":     session_store["user_id"],
                                "sessionId":  session_store["session_id"],
                                "forAccount": MYADMIN_ACCOUNT,
                                "nextId":     next_id,
                            },
                            timeout=120.0,
                        )
                        batch = result.get("result") or []
                        if not batch:
                            break
                        all_device_dbs.extend(batch)
                        if len(batch) < 1000:
                            break
                        next_id = batch[-1].get("Id") or batch[-1].get("id") or 0

                    if all_device_dbs:
                        _sync_cache["device_db_records"]      = all_device_dbs
                        _sync_cache["device_db_refreshed_at"] = time.time()
                        if _sync_cache.get("contracts"):
                            _save_json(SYNC_CACHE_FILE, _sync_cache)
                        print(f"[bg-refresh] Step-1 complete: "
                              f"{len(all_device_dbs):,} device-db records updated.")
            else:
                print("[bg-refresh] Skipping -- no session or manual sync in progress.")

        except Exception as exc:
            print(f"[bg-refresh] Unexpected error (non-fatal): {exc}")

        await asyncio.sleep(step1_interval)


def start_background_refresh() -> None:
    """Schedule the background refresh loop.
    Call once from the FastAPI lifespan / startup hook.
    """
    global _bg_refresh_task
    if _bg_refresh_task is None or _bg_refresh_task.done():
        _bg_refresh_task = asyncio.get_event_loop().create_task(
            _background_device_db_refresh()
        )
        print(f"[bg-refresh] Scheduled: Step-1 every {DEVICE_DB_REFRESH_MINUTES} min, "
              f"full sync every {CACHE_TTL_HOURS}h.")


# --- Billing type map ---------------------------------------------------------
# Maps QB Job Type values (lower-cased) to internal billing type strings.
# QB Job Type is often a compound value like "Charge Upon Activation:Hanover"
# or "Han-CS" — the primary type is the segment AFTER the last colon.
# We try exact match on the full key first, then on the primary segment only.
QB_JOB_TYPE_MAP = {
    "standard":                     "Standard",
    "cua":                          "Charge Upon Activation",
    "charge upon activation":       "Charge Upon Activation",
    "cua - charge upon activation": "Charge Upon Activation",
    "sourcewell":                   "Sourcewell",
    "hanover":                      "Hanover",
    "hanover deal":                 "Hanover",
    "han-cs":                       "Han-CS",
    "hancs":                        "Han-CS",
    "check before sending":         "Check Before Sending",
    "reseller":                     "Reseller",
    "in collections":               "In Collections",
    "collections":                  "In Collections",
    "terminated":                   "Terminated",
}

def normalize(name: str) -> str:
    return name.strip().lower()


_HAN_CS_SUFFIX_LOWER = "{han-cs}"

def _strip_han_cs(name: str) -> str:
    """Strip the '{Han-CS}' suffix from a customer name for QB lookups.

    MyAdmin stores Han-CS customers as e.g. 'ACES Controls LLC {Han-CS}'.
    QuickBooks has only 'ACES Controls LLC' as the customer name.  When
    looking up QB data (billing type, account number, etc.) for a Han-CS
    customer we must strip the suffix so the lookup succeeds.

    Examples:
      'ACES Controls LLC {Han-CS}'   -> 'ACES Controls LLC'
      'ACES Controls LLC'            -> 'ACES Controls LLC'  (unchanged)
      'Hoopaugh Grading LLC'         -> 'Hoopaugh Grading LLC'  (unchanged)
    """
    stripped = name.strip()
    lower    = stripped.lower()
    if lower.endswith(_HAN_CS_SUFFIX_LOWER):
        return stripped[: -len(_HAN_CS_SUFFIX_LOWER)].strip()
    return stripped


def _strip_sub_account_suffix(name: str) -> str:
    """Strip a non-Han-CS sub-account brace suffix for QB lookups.

    Sub-accounts in MyAdmin are named like 'AWT Construction Group Inc. {3rd Party Devices}'.
    QuickBooks only has the parent name 'AWT Construction Group Inc.', so the brace
    suffix must be removed before doing a QB lookup.

    Rules:
      - If there is no '{', return unchanged.
      - If the FIRST token inside braces is 'han-cs', preserve '{Han-CS}' but strip
        any additional brace suffix that follows (e.g. '{Cameras}', '{3rd Party}').
        '{Han-CS}' is an identity qualifier handled separately by _strip_han_cs().
      - Otherwise, strip everything from the first '{' onward.

    Examples:
      'AWT Construction Group Inc. {3rd Party Devices}' -> 'AWT Construction Group Inc.'
      'ACES Controls LLC {Han-CS} {Cameras}'            -> 'ACES Controls LLC {Han-CS}'
      'ACES Controls LLC {Han-CS} {Sub}'                -> 'ACES Controls LLC {Han-CS}'
      'ACES Controls LLC {Han-CS}'                      -> 'ACES Controls LLC {Han-CS}'  (unchanged)
      'Normal Customer Name'                            -> 'Normal Customer Name'  (unchanged)
    """
    idx = name.find("{")
    if idx == -1:
        return name
    close = name.find("}", idx)
    first_token = name[idx + 1 : close].strip() if close != -1 else ""
    if first_token.lower() == "han-cs":
        # Preserve the '{Han-CS}' identity qualifier but strip any further
        # brace suffix that follows (e.g. '{Cameras}', '{3rd Party Devices}').
        han_cs_end = close + 1  # index just after the closing '}' of {Han-CS}
        second_idx = name.find("{", han_cs_end)
        if second_idx != -1:
            return name[:second_idx].strip()  # strip everything from '{Cameras}' onward
        return name  # no second suffix — leave as-is
    return name[:idx].strip()


def _clean_name(s: str) -> str:
    """Decode HTML entities in a MyAdmin company name.
    MyAdmin returns names like 'Aqua Hero Pool &amp; Spa Service';
    this converts them to 'Aqua Hero Pool & Spa Service'.
    """
    return _html.unescape(s or "").strip()

def map_billing_type(job_type: str) -> str:
    """Map a QB Job Type string to a GeoBridge billing type.

    QB Job Type is sometimes a compound value like "Charge Upon Activation:Hanover"
    where the primary type is the segment BEFORE the colon and the qualifier
    (e.g. the insurance program) is AFTER.  We:
      1. Try an exact match on the full lower-cased value.
      2. If no hit, try each colon-delimited segment from right to left
         (last segment = most specific qualifier, first = base type).
      3. Still no hit → "Unknown".

    Examples:
      "HANOVER"                       -> "Hanover"   (exact)
      "Han-CS"                        -> "Han-CS"    (exact)
      "Charge Upon Activation:Hanover"-> "CUA"       (first segment)
      "Charge Upon Activation:Han-CS" -> "Han-CS"    (last segment, more specific)
      "Standard:Hanover"              -> "Hanover"   (last segment wins)
    """
    if not job_type:
        return "Unknown"
    full_key = normalize(job_type)
    # 1. Exact match on full string
    if full_key in QB_JOB_TYPE_MAP:
        return QB_JOB_TYPE_MAP[full_key]
    # 2. Split on ':' — check segments right-to-left (most specific first)
    segments = [s.strip() for s in full_key.split(":") if s.strip()]
    for seg in reversed(segments):
        if seg in QB_JOB_TYPE_MAP:
            return QB_JOB_TYPE_MAP[seg]
    return "Unknown"

def enrich_customer(customer: dict) -> dict:
    """Merge grouped MyAdmin record with QB data and billing overrides."""
    company_id   = str(customer.get("companyId") or "")
    company_name = customer.get("customerName") or ""
    db_name      = customer.get("primaryDatabase") or ""

    # Build the QB lookup name by stripping suffixes in order:
    #   1. Strip any non-Han-CS sub-account suffix, e.g. '{3rd Party Devices}',
    #      so 'AWT Construction Group Inc. {3rd Party Devices}' looks up
    #      'AWT Construction Group Inc.' and inherits the parent's billing type.
    #   2. Strip '{Han-CS}' identity suffix, so 'ACES Controls LLC {Han-CS}'
    #      looks up 'ACES Controls LLC' in QB.
    # This ensures both sub-accounts and Han-CS customers resolve correctly.
    qb_lookup_name = _strip_han_cs(_strip_sub_account_suffix(company_name))
    qb = qb_customers.get(normalize(qb_lookup_name)) or {}

    # Display name: always use the original MyAdmin company_name so that
    # sub-account suffixes ({Cameras}, {3rd Party Devices}, etc.) are preserved.
    # Previously we used qb.get("name") here for non-Han-CS customers, which
    # silently stripped those suffixes — the QB parent record's name has no
    # suffix, so all sub-accounts collapsed to the same display name and the
    # frontend couldn't group them correctly under their parent.
    is_han_cs_customer = company_name.strip().lower().endswith(_HAN_CS_SUFFIX_LOWER)
    display_name = company_name or qb.get("name") or f"Company {company_id}"
    cid = company_id or normalize(company_name)

    # Billing type priority:
    #   1. Manual override (billing_overrides.json)
    #   2. QB Job Type (from qb_customers lookup — parent name after stripping suffixes)
    #      Sub-accounts (e.g. 'AWT... {3rd Party Devices}') look up the parent QB
    #      record and inherit its billing type here.
    #   3. If the company name has '{Han-CS}' suffix and QB has no override,
    #      default to 'Han-CS' (the name itself tells us the type).
    #   4. Fall back to 'Unknown'
    billing_type = (
        billing_overrides.get(cid)
        or qb.get("billingType")
        or ("Han-CS" if is_han_cs_customer else None)
        or "Unknown"
    )

    return {
        "id":              cid,
        "name":            display_name,
        "accountNo":       qb.get("accountNo") or "",
        "billingType":     billing_type,
        "primaryDatabase": db_name,
        "deviceCount":     customer.get("activeDevices") or 0,
        "terms":           qb.get("terms") or "",
        "balance":         float(qb.get("balance") or 0),
        "hasQbData":       bool(qb),
        "email":           customer.get("email") or "",
        "phone":           customer.get("phone") or "",
        "address":         customer.get("address") or "",
    }


async def _fetch_myadmin_customers(force_refresh: bool = False) -> List[dict]:
    """
    Pull customer + device data from MyAdmin using TWO steps.

    Step 1 -- GetCurrentDeviceDatabases (FAST, ~10-30s total)
        Returns: device serial, DeviceId, DatabaseName (real Geotab DB name)

    Step 2 -- GetDeviceContractsByPage (SLOW, ~2-5min, but cached 12h)
        Returns: userCompany.name (customer name), isTerminated, device.id

    Progress is emitted to _sync_progress so the SSE endpoint can stream it
    to the frontend in real time.
    """
    # -- Reset / start progress -------------------------------------------------
    _set_progress(
        active=True,
        step="step1",
        step_label="Step 1/2 -- Fetching device databases...",
        page=0,
        total_pages_est=0,
        records=0,
        pct=2,
        message="Contacting MyAdmin...",
        error="",
    )

    # -- Step 1: GetCurrentDeviceDatabases ------------------------------------
    print("[sync] Step 1: Fetching current device databases (fast)...")
    all_device_dbs = []
    next_id = 0
    page_num = 0
    while True:
        page_num += 1
        _set_progress(
            step="step1",
            step_label=f"Step 1/2 -- Fetching device databases (page {page_num})...",
            page=page_num,
            pct=min(2 + page_num * 3, 20),   # grows to ~20% during step 1
            message=f"Device database page {page_num}...",
        )
        result = await myadmin_call(
            "GetCurrentDeviceDatabases",
            {
                "apiKey":     session_store["user_id"],
                "sessionId":  session_store["session_id"],
                "forAccount": MYADMIN_ACCOUNT,
                "nextId":     next_id,
            },
            timeout=120.0,
        )
        batch = result.get("result") or []
        print(f"[sync] Step 1 page {page_num}: {len(batch)} device-db records")
        if not batch:
            break
        all_device_dbs.extend(batch)
        if len(batch) < 1000:
            break
        next_id = batch[-1].get("Id") or batch[-1].get("id") or 0

    print(f"[sync] Step 1 complete: {len(all_device_dbs)} total device-db records")
    # Persist the device-db map so get_customer() can resolve real DB names
    _sync_cache["device_db_records"] = all_device_dbs
    _set_progress(
        step="step1",
        step_label="Step 1/2 -- Device databases fetched ok",
        pct=20,
        message=f"{len(all_device_dbs):,} device-db records",
    )

    # -- Step 2: GetDeviceContractsByPage -------------------------------------
    cache_age = time.time() - (_sync_cache.get("fetched_at") or 0)
    cache_ok  = (
        not force_refresh
        and _sync_cache.get("contracts")
        and cache_age < CACHE_TTL_HOURS * 3600
    )

    if cache_ok:
        print(f"[sync] Step 2: Using cached contracts ({cache_age/3600:.1f}h old)")
        all_contracts = _sync_cache["contracts"]
        _set_progress(
            step="step2",
            step_label="Step 2/2 -- Using cached contracts ok",
            pct=75,
            message=f"{len(all_contracts):,} contracts from 12-hour cache",
        )
    else:
        print("[sync] Step 2: Fetching device contracts (slow -- customer names)...")
        all_contracts = []
        next_id  = 0
        page_num = 0

        # We'll estimate ~120 pages for CELU01 (100k+ contracts / 1000 per page).
        # The bar runs from 20% -> 75% during step 2.
        STEP2_START_PCT = 20
        STEP2_END_PCT   = 75
        STEP2_PCT_RANGE = STEP2_END_PCT - STEP2_START_PCT
        EST_PAGES       = 120   # conservative estimate; recalculates as we go

        _set_progress(
            step="step2",
            step_label="Step 2/2 -- Fetching device contracts...",
            pct=STEP2_START_PCT,
            message="Starting contract fetch...",
        )

        while True:
            page_num += 1
            # Progress within step 2: linear up to 95% of the step's range,
            # then clamp -- we don't know total pages until the last batch < 1000.
            step2_fraction = min(page_num / max(EST_PAGES, page_num + 1), 0.95)
            current_pct    = int(STEP2_START_PCT + step2_fraction * STEP2_PCT_RANGE)
            _set_progress(
                step="step2",
                step_label=f"Step 2/2 -- Fetching contracts (page {page_num})...",
                page=page_num,
                total_pages_est=max(EST_PAGES, page_num),
                records=len(all_contracts),
                pct=current_pct,
                message=f"Page {page_num} * {len(all_contracts):,} contracts so far...",
            )
            print(f"[sync] Step 2 page {page_num} (nextId={next_id})...")
            result = await myadmin_call(
                "GetDeviceContractsByPage",
                {
                    "apiKey":                  session_store["user_id"],
                    "sessionId":               session_store["session_id"],
                    "forAccount":              MYADMIN_ACCOUNT,
                    "nextId":                  next_id,
                    # Required to populate firstDeviceActivationDate (First Connect Date)
                    "includesDeviceConnectionInfo": True,
                },
                timeout=120.0,
            )
            batch = result.get("result") or []
            print(f"[sync] Step 2 page {page_num}: {len(batch)} contracts")
            if not batch:
                break
            all_contracts.extend(batch)
            if len(batch) < 1000:
                break
            next_id = batch[-1].get("id", 0)

        print(f"[sync] Step 2 complete: {len(all_contracts)} total contracts")
        _set_progress(
            step="step2",
            step_label="Step 2/2 -- Contracts fetched ok",
            records=len(all_contracts),
            pct=75,
            message=f"{len(all_contracts):,} total contracts fetched",
        )
        _sync_cache["contracts"]         = all_contracts
        _sync_cache["fetched_at"]         = time.time()
        _sync_cache["device_db_records"]  = all_device_dbs   # keep in sync with contracts
        _save_json(SYNC_CACHE_FILE, _sync_cache)

    # -- Processing: join + group -----------------------------------------------
    _set_progress(
        step="processing",
        step_label="Processing -- Joining device & contract data...",
        pct=80,
        message="Building customer list...",
    )

    device_id_to_company: Dict[str, dict] = {}
    for c in all_contracts:
        device   = c.get("device") or {}
        dev_id   = str(device.get("id") or "")
        if not dev_id:
            continue
        uc       = c.get("userContact") or {}
        company  = uc.get("userCompany") or {}
        device_id_to_company[dev_id] = {
            "companyId":   str(company.get("id") or ""),
            "companyName": _clean_name(company.get("name") or ""),
            "terminated":  bool(c.get("isTerminated")),
        }

    _set_progress(pct=85, message="Grouping by company...")

    # Build a deviceId → db_name lookup from Step 1 results so we can attach
    # a database name even when we encounter a device via the contracts pass.
    device_id_to_db: Dict[str, str] = {}
    for rec in all_device_dbs:
        dev_id  = str(rec.get("DeviceId") or rec.get("deviceId") or "")
        db_name = rec.get("DatabaseName") or rec.get("databaseName") or ""
        if dev_id and db_name:
            device_id_to_db[dev_id] = db_name

    company_map: Dict[str, dict] = {}

    # ── Pass 1: iterate device-DB records (Step 1 data) ───────────────────
    # Covers all devices that are currently in an active Geotab database.
    for rec in all_device_dbs:
        dev_id   = str(rec.get("DeviceId") or rec.get("deviceId") or "")
        db_name  = rec.get("DatabaseName") or rec.get("databaseName") or ""

        contract_info = device_id_to_company.get(dev_id) or {}
        company_id    = contract_info.get("companyId") or ""
        company_name  = contract_info.get("companyName") or ""
        terminated    = contract_info.get("terminated", False)

        key = company_id or db_name
        if not key:
            continue

        if key not in company_map:
            company_map[key] = {
                "companyId":       company_id,
                "customerName":    company_name,
                "primaryDatabase": db_name,
                "activeDevices":   0,
                "totalDevices":    0,
            }

        if not company_map[key]["customerName"] and company_name:
            company_map[key]["customerName"] = company_name
        if not company_map[key]["primaryDatabase"] and db_name:
            company_map[key]["primaryDatabase"] = db_name

        company_map[key]["totalDevices"] += 1
        if not terminated:
            company_map[key]["activeDevices"] += 1

    # ── Pass 2: iterate contracts (Step 2 data) ───────────────────────────
    # Catches companies whose devices exist in MyAdmin contracts but did NOT
    # appear in the device-DB records (e.g. sub-accounts whose devices haven't
    # been assigned to a Geotab database yet, or are missing from Step 1).
    # We only add/update entries here — we never reduce counts set by Pass 1.
    for c in all_contracts:
        device   = c.get("device") or {}
        dev_id   = str(device.get("id") or "")
        uc       = c.get("userContact") or {}
        company  = uc.get("userCompany") or {}
        company_id   = str(company.get("id") or "")
        company_name = _clean_name(company.get("name") or "")
        terminated   = bool(c.get("isTerminated"))

        key = company_id
        if not key:
            continue

        if key not in company_map:
            # Company entirely missing from Pass 1 — add it now
            db_name = device_id_to_db.get(dev_id) or ""
            company_map[key] = {
                "companyId":       company_id,
                "customerName":    company_name,
                "primaryDatabase": db_name,
                "activeDevices":   0,
                "totalDevices":    0,
            }

        # Fill in missing name / db if Pass 1 left them blank
        if not company_map[key]["customerName"] and company_name:
            company_map[key]["customerName"] = company_name
        if not company_map[key]["primaryDatabase"] and dev_id:
            db = device_id_to_db.get(dev_id) or ""
            if db:
                company_map[key]["primaryDatabase"] = db

        # Only count devices NOT already counted in Pass 1
        # (i.e. devices whose dev_id was NOT in device_id_to_db)
        if dev_id and dev_id not in device_id_to_db:
            company_map[key]["totalDevices"] += 1
            if not terminated:
                company_map[key]["activeDevices"] += 1

    raw = [
        v for v in company_map.values()
        if v["activeDevices"] > 0
        and not v["customerName"].startswith("* Terminated")
    ]
    _print(f"[sync] {len(all_device_dbs)} device-db records + {len(all_contracts)} contracts -> "
           f"{len(company_map)} companies -> {len(raw)} with active devices")

    # -- Rebuild name -> companyId lookup -------------------------------------
    global name_to_company_id
    name_to_company_id = {
        normalize(v["customerName"]): v["companyId"]
        for v in raw if v["customerName"]
    }

    # -- Done -----------------------------------------------------------------
    _set_progress(
        active=False,
        step="done",
        step_label="Sync complete ok",
        pct=100,
        message=f"{len(raw):,} customers loaded",
    )

    return raw


# --- GET /api/customers/sync-progress  (SSE -- MUST be before wildcard route) -
@router.get("/customers/sync-progress")
async def sync_progress_sse():
    """
    Server-Sent Events endpoint that streams _sync_progress as JSON.

    Timing contract:
      - The frontend opens this SSE connection BEFORE firing GET /api/customers.
      - We wait up to 5 s for the sync to become active (active=True).
      - Once active we stream until done/error, then send a final event and close.
      - If the sync never becomes active within 5 s we close with the idle state.
    """
    async def event_stream():
        # -- Phase 1: wait for the sync to start (up to 5 s) ------------------
        waited = 0.0
        while not _sync_progress["active"] and waited < 5.0:
            yield f"data: {json.dumps(_sync_progress)}\n\n"
            await asyncio.sleep(0.3)
            waited += 0.3

        if not _sync_progress["active"]:
            # Sync never started -- close the stream
            yield f"data: {json.dumps(_sync_progress)}\n\n"
            return

        # -- Phase 2: stream while active -------------------------------------
        last_json = None
        while _sync_progress["active"] or _sync_progress["step"] not in ("done", "error", ""):
            current = dict(_sync_progress)
            current_json = json.dumps(current)
            if current_json != last_json:
                yield f"data: {current_json}\n\n"
                last_json = current_json
            await asyncio.sleep(0.3)

        # -- Final flush -------------------------------------------------------
        yield f"data: {json.dumps(_sync_progress)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# --- GET /api/customers --------------------------------------------------------
@router.get("/customers")
async def get_customers(
    page:         int = Query(1, ge=1),
    page_size:    int = Query(50, ge=1, le=200),
    search:       str = Query(""),
    billing_type: str = Query(""),
    force_refresh: bool = Query(False),
):
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in")

    try:
        cache_age = time.time() - (_sync_cache.get("customer_fetched_at") or 0)
        use_cache = bool(
            not force_refresh
            and _sync_cache.get("raw_customers")
            and cache_age < CACHE_TTL_HOURS * 3600
        )

        if use_cache:
            print(f"[customers] Using cached customer list ({cache_age/3600:.1f}h old)")
            raw = _sync_cache["raw_customers"]
        else:
            # Use a lock so concurrent page-load requests don't all trigger
            # simultaneous full syncs -- only the first one fetches, the rest
            # wait and then use the freshly-populated cache.
            async with _sync_lock:
                # Re-check cache inside the lock (another request may have
                # just finished fetching while we were waiting)
                cache_age2 = time.time() - (_sync_cache.get("customer_fetched_at") or 0)
                if (
                    not force_refresh
                    and _sync_cache.get("raw_customers")
                    and cache_age2 < CACHE_TTL_HOURS * 3600
                ):
                    print(f"[customers] Lock: using cache populated by concurrent request ({cache_age2/3600:.1f}h old)")
                    raw = _sync_cache["raw_customers"]
                else:
                    raw = await _fetch_myadmin_customers(force_refresh=force_refresh)
                    _sync_cache["raw_customers"]       = raw
                    _sync_cache["customer_fetched_at"] = time.time()
                    _save_json(SYNC_CACHE_FILE, _sync_cache)

        customers = [enrich_customer(c) for c in raw]

        if search:
            s = search.lower()
            customers = [
                c for c in customers
                if s in c["name"].lower()
                or s in (c["accountNo"] or "").lower()
                or s in (c["primaryDatabase"] or "").lower()
            ]

        if billing_type:
            customers = [c for c in customers if c["billingType"] == billing_type]

        customers.sort(key=lambda c: c["name"].lower())

        return {
            "customers":     customers,
            "page":          1,
            "pageSize":      len(customers),
            "hasMore":       False,
            "total":         len(customers),
            "fromCache":     use_cache,
            "cacheAgeHours": round(cache_age / 3600, 1),
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        _set_progress(active=False, step="error", pct=0, error=str(e),
                      message=f"Error: {e}", step_label="Sync failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- GET /api/customers/qb-data/summary ---------------------------------------
@router.get("/customers/qb-data/summary")
async def get_qb_summary():
    if not qb_customers:
        return {
            "customersLoaded":      0,
            "itemsLoaded":          len(qb_items),
            "billingTypeBreakdown": {},
        }
    breakdown: Dict[str, int] = {}
    for qb in qb_customers.values():
        bt = qb.get("billingType") or "Unknown"
        breakdown[bt] = breakdown.get(bt, 0) + 1
    return {
        "customersLoaded":      len(qb_customers),
        "itemsLoaded":          len(qb_items),
        "billingTypeBreakdown": breakdown,
    }


# --- GET /api/dashboard/stats -------------------------------------------------
@router.get("/dashboard/stats")
async def get_dashboard_stats():
    """Return summary counts for the dashboard home page."""
    raw = _sync_cache.get("raw_customers") or []
    fetched_at = _sync_cache.get("customer_fetched_at")

    if not raw:
        return {
            "totalCustomers":   0,
            "totalDevices":     0,
            "billingBreakdown": {},
            "cacheAgeHours":    None,
            "hasCachedData":    False,
        }

    # Enrich the same way get_customers() does so billingType + deviceCount are accurate
    customers = [enrich_customer(c) for c in raw]

    total_customers = len(customers)
    total_devices   = sum(c.get("deviceCount") or 0 for c in customers)

    billing_breakdown: Dict[str, int] = {}
    for c in customers:
        bt = c.get("billingType") or "Unknown"
        billing_breakdown[bt] = billing_breakdown.get(bt, 0) + 1

    cache_age_hours = round((time.time() - fetched_at) / 3600, 1) if fetched_at else None

    return {
        "totalCustomers":   total_customers,
        "totalDevices":     total_devices,
        "billingBreakdown": billing_breakdown,
        "cacheAgeHours":    cache_age_hours,
        "hasCachedData":    True,
    }


# --- POST /api/customers/import-qb --------------------------------------------
@router.post("/customers/import-qb")
async def import_qb_customers(file: UploadFile = File(...)):
    global qb_customers
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows   = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty")

    # Capture the actual column headers so we can return them for debugging
    csv_columns = list(rows[0].keys()) if rows else []

    imported  = 0
    skipped   = 0
    protected = 0

    for row in rows:
        row = {k.strip(): (v or "").strip() for k, v in row.items()}

        name = (
            row.get("Customer") or row.get("Name")
            or row.get("Customer Name") or row.get("Full Name") or ""
        )
        if not name:
            skipped += 1
            continue

        job_type    = row.get("Job Type") or row.get("Customer Type") or row.get("Type") or ""
        account_no  = (
            row.get("Account No.") or row.get("Account Number")
            or row.get("Account #") or row.get("Acct No") or ""
        )
        terms       = row.get("Terms") or row.get("Payment Terms") or ""
        qb_class    = row.get("Class") or row.get("QB Class") or ""
        balance_str = (
            row.get("Balance Total") or row.get("Balance")
            or row.get("Current Balance") or "0"
        )
        balance_str = balance_str.replace("$", "").replace(",", "").strip()
        if balance_str.startswith("(") and balance_str.endswith(")"):
            balance_str = "-" + balance_str[1:-1]
        try:
            balance = float(balance_str) if balance_str else 0.0
        except ValueError:
            balance = 0.0

        new_billing_type = map_billing_type(job_type)
        norm_name        = normalize(name)
        company_id       = name_to_company_id.get(norm_name, "")

        has_override = bool(billing_overrides.get(company_id))
        existing     = qb_customers.get(norm_name) or {}
        if has_override:
            preserved_billing = existing.get("billingType") or new_billing_type
            protected += 1
            _print(f"[import-qb] Override protected '{name}': "
               f"QB='{new_billing_type}' -> keeping '{preserved_billing}'")
        else:
            preserved_billing = new_billing_type

        qb_customers[norm_name] = {
            "name":        name,
            "accountNo":   account_no,
            "billingType": preserved_billing,
            "jobType":     job_type,
            "terms":       terms,
            "qbClass":     qb_class,
            "balance":     balance,
            # QB exports address as free-form lines in columns "Bill to 1"–"Bill to 5".
            # Store all five as-is; the PDF renderer skips line 1 when it
            # duplicates the customer name (QB always repeats it there).
            "billTo1":     row.get("Bill to 1", "").strip(),
            "billTo2":     row.get("Bill to 2", "").strip(),
            "billTo3":     row.get("Bill to 3", "").strip(),
            "billTo4":     row.get("Bill to 4", "").strip(),
            "billTo5":     row.get("Bill to 5", "").strip(),
        }
        imported += 1

    _save_json(QB_DATA_FILE, qb_customers)
    # Persist the column list so the debug endpoint can report it later
    _save_json(os.path.join(_HERE, "qb_last_import_columns.json"), csv_columns)
    print(f"[import-qb] Saved {len(qb_customers)} QB customers to {QB_DATA_FILE}")

    msg = f"{imported} customers imported, {skipped} skipped"
    if protected:
        msg += f", {protected} GeoBridge billing overrides preserved"

    return {
        "success":    True,
        "message":    msg,
        "imported":   imported,
        "skipped":    skipped,
        "protected":  protected,
        "total":      len(qb_customers),
        "csvColumns": csv_columns,   # actual column names seen in this CSV — useful for debugging address mapping
    }





# --- GET /api/debug/qb-columns — show CSV columns from last QB import ---------
@router.get("/debug/qb-columns")
async def debug_qb_columns():
    """Return the column headers that were present in the most recently imported QB CSV."""
    cols = _load_json(os.path.join(_HERE, "qb_last_import_columns.json"), None)
    if cols is None:
        raise HTTPException(status_code=404, detail="No QB import has been performed yet, or column log is missing.")
    return {"csvColumns": cols, "count": len(cols)}


# --- GET /api/debug/qb-customer/{name} — inspect stored QB record -------------
@router.get("/debug/qb-customer/{name}")
async def debug_qb_customer(name: str):
    """Return the stored QB customer record for a given name (fuzzy-normalised lookup)."""
    from .customers import _normalize as _n
    key = _n(name)
    record = qb_customers.get(key)
    if record is None:
        # Try partial match
        matches = {k: v for k, v in qb_customers.items() if name.lower() in k}
        if not matches:
            raise HTTPException(status_code=404, detail=f"No QB record found for '{name}'. Normalised key tried: '{key}'")
        return {"partialMatches": matches}
    return {"key": key, "record": record}


# --- GET /api/customers/{account_id} ------------------------------------------
@router.get("/customers/{account_id}")
async def get_customer(account_id: str):
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in")

    try:
        all_contracts = _sync_cache.get("contracts") or []
        if not all_contracts:
            result = await myadmin_call(
                "GetDeviceContractsByPage",
                {
                    "apiKey":                  session_store["user_id"],
                    "sessionId":               session_store["session_id"],
                    "forAccount":              MYADMIN_ACCOUNT,
                    "nextId":                  0,
                    # Required to populate firstDeviceActivationDate (First Connect Date)
                    "includesDeviceConnectionInfo": True,
                },
                timeout=120.0,
            )
            all_contracts = result.get("result") or []

        # Only include non-terminated contracts (Active + Never Billed)
        matching = [
            c for c in all_contracts
            if str(((c.get("userContact") or {}).get("userCompany") or {}).get("id") or "") == account_id
            and not c.get("isTerminated", False)
        ]

        # Build a deviceId -> DatabaseName lookup from the cached device-db map
        # so we can show the real DB name (e.g. "bluearrow") instead of a numeric ID
        device_db_map: Dict[str, str] = {}
        for rec in (_sync_cache.get("device_db_records") or []):
            dev_id  = str(rec.get("DeviceId") or rec.get("deviceId") or "")
            db_name = rec.get("DatabaseName") or rec.get("databaseName") or ""
            if dev_id and db_name:
                device_db_map[dev_id] = db_name

        normalized = []
        for d in matching:
            device = d.get("device") or {}
            dev_id = str(device.get("id") or "")

            # -- Active Billing Plan: activeDevicePlan.name --------------------
            # Confirmed field from API: d["activeDevicePlan"]["name"]
            # e.g. "Pro Mode", "Base Mode: Live"
            adp = d.get("activeDevicePlan") or {}
            active_billing_plan = adp.get("name") or ""

            # -- Rate Plan Code: promoCode -------------------------------------
            # Confirmed from contract inspection: the rate plan code shown in
            # MyAdmin (e.g. "CELU-TP-250", "SWELL-NOINS3") lives at top level
            # as promoCode, not productCode or ratePlanName.
            rate_plan_code = (d.get("promoCode") or "").upper()

            # -- Database: latestDeviceDatabase.databaseName -------------------
            # Confirmed field from API -- directly on the contract, no lookup needed
            # e.g. "wray_roofing"
            ldd = d.get("latestDeviceDatabase") or {}
            db_name = ldd.get("databaseName") or device_db_map.get(dev_id) or ""

            # -- Dates: trim ISO datetime to yyyy-mm-dd ------------------------
            # Guard against .NET DateTime.MinValue sentinel "0001-01-01T00:00:00"
            # which MyAdmin returns for fields that have no date set.  Treat any
            # date starting with "0001" as empty so it doesn't pollute the UI.
            def _date(raw: str) -> str:
                s = (raw or "")[:10]
                return "" if s.startswith("0001") else s

            # Determine activation status.  Terminated contracts are already
            # filtered out above, so the only remaining distinction is whether
            # the device has ever been activated (non-blank billing plan).
            _adp_upper = active_billing_plan.upper()
            _is_never_activated = (
                not active_billing_plan
                or _adp_upper == "NEVER ACTIVATED"
                or "never" in _adp_upper
            )
            _serial = device.get("serialNumber") or ""

            # Manual billing-date override takes highest priority.
            # If the user has set a date via the UI it shows immediately here
            # and is also used by the invoice engine (same store).
            _override_date = billing_date_overrides.get(_serial.strip().upper())

            # Display date: override > First Connect Date > Billing Start Date > startDate
            # Column header stays "Billing Start Date" in the UI.
            _api_start_date = _date(
                d.get("firstDeviceActivationDate")
                or d.get("billingStartDate")
                or d.get("startDate")
                or ""
            )
            _display_start_date = _override_date or _api_start_date

            normalized.append({
                "serialNumber":      _serial,
                "deviceType":        (device.get("deviceType") or {}).get("name") or "",
                "activeBillingPlan": active_billing_plan,
                "ratePlanCode":      rate_plan_code,
                "database":          db_name,
                "status":            "Never Activated" if _is_never_activated else "Active",
                "contractStartDate": _display_start_date,
                "hasDateOverride":   _override_date is not None,
                "firstConnectDate":  _date(d.get("firstDeviceActivationDate") or ""),
                "contractEndDate":   _date(d.get("endDate") or ""),
            })

        return {
            "customerId": account_id,
            "devices":    normalized,
            "total":      len(normalized),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- POST /api/customers/{account_id}/billing-type ---------------------------
class BillingTypeUpdate(BaseModel):
    billing_type: str

@router.post("/customers/{account_id}/billing-type")
async def set_billing_type(account_id: str, body: BillingTypeUpdate):
    valid = [
        "Standard", "Charge Upon Activation", "Sourcewell", "Hanover", "Han-CS",
        "Check Before Sending",
        "Reseller", "In Collections", "Terminated", "Unknown",
    ]
    if body.billing_type not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid billing type: {body.billing_type}")
    billing_overrides[account_id] = body.billing_type
    _save_json(OVERRIDES_FILE, billing_overrides)
    return {"success": True, "customerId": account_id, "billingType": body.billing_type}


# --- GET /api/customers/{account_id}/devices ---------------------------------
@router.get("/customers/{account_id}/devices")
async def get_customer_devices(account_id: str):
    return await get_customer(account_id)


# =============================================================================
#  DEBUG: raw contract dump
# =============================================================================

@router.get("/debug/contract/{serial}")
async def debug_contract(serial: str):
    """
    Return the raw cached contract record(s) for a given serial number.
    Useful for diagnosing missing date fields or billing type issues.
    No auth check — local dev/desktop use only.
    """
    contracts: List[dict] = _sync_cache.get("contracts") or []
    if not contracts:
        raise HTTPException(status_code=503, detail="No contract data cached. Run a MyAdmin sync first.")

    # Match on device.serialNumber (case-insensitive)
    serial_upper = serial.strip().upper()
    matches = []
    for c in contracts:
        dev = (c.get("device") or {})
        if (dev.get("serialNumber") or "").upper() == serial_upper:
            matches.append(c)

    if not matches:
        raise HTTPException(status_code=404, detail=f"No cached contract found for serial '{serial}'")

    # Return raw data plus a convenience summary of the key date fields
    summaries = []
    for c in matches:
        uc      = c.get("userContact") or {}
        company = (uc.get("userCompany") or {})
        dev     = c.get("device") or {}
        summaries.append({
            "serial":                      dev.get("serialNumber"),
            "companyId":                   company.get("id"),
            "companyName":                 company.get("name"),
            "isTerminated":                c.get("isTerminated"),
            "billingStartDate":            c.get("billingStartDate"),
            "firstDeviceActivationDate":   c.get("firstDeviceActivationDate"),
            "endDate":                     c.get("endDate"),
            "promoCode":                   c.get("promoCode"),
            "activeDevicePlan":            (c.get("activeDevicePlan") or {}).get("name"),
        })

    return {
        "serial":    serial,
        "count":     len(matches),
        "summaries": summaries,
        "raw":       matches,
    }


# =============================================================================
#  Manual billing-date overrides  (per-device)
# =============================================================================

class BillingDateOverride(BaseModel):
    billingStartDate: str   # "YYYY-MM-DD"


@router.post("/customers/device/{serial}/billing-date")
async def set_device_billing_date(serial: str, body: BillingDateOverride):
    """
    Manually set (or replace) the billing start date for a device serial.
    Stored in billing_date_overrides.json as {"SERIAL": "YYYY-MM-DD"}.
    This date takes priority over the MyAdmin API dates in invoice generation.
    """
    from datetime import date as _date_cls
    # Validate date format
    try:
        _date_cls.fromisoformat(body.billingStartDate)
    except ValueError:
        raise HTTPException(status_code=400, detail="billingStartDate must be YYYY-MM-DD")

    key = serial.strip().upper()
    billing_date_overrides[key] = body.billingStartDate
    _save_json(BILLING_DATE_OVERRIDES_FILE, billing_date_overrides)
    return {"success": True, "serial": key, "billingStartDate": body.billingStartDate}


@router.delete("/customers/device/{serial}/billing-date")
async def delete_device_billing_date(serial: str):
    """
    Remove the manual billing start date override for a device serial.
    After deletion the invoice engine falls back to MyAdmin API dates.
    """
    key = serial.strip().upper()
    if key not in billing_date_overrides:
        raise HTTPException(status_code=404, detail=f"No billing date override found for serial '{serial}'")

    del billing_date_overrides[key]
    _save_json(BILLING_DATE_OVERRIDES_FILE, billing_date_overrides)
    return {"success": True, "serial": key, "cleared": True}


@router.get("/customers/device/{serial}/billing-date")
async def get_device_billing_date(serial: str):
    """Return the current manual billing date override for a serial (if any)."""
    key = serial.strip().upper()
    override = billing_date_overrides.get(key)
    return {
        "serial":             key,
        "hasOverride":        override is not None,
        "billingStartDate":   override,
    }
