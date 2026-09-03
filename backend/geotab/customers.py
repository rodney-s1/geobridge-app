from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
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
from .auth import myadmin_call, myadmin_call_v3, require_session, session_store

# --- Windows-safe print (avoids CP1252 UnicodeEncodeError on arrow chars) -----
def _print(*args, **kwargs):
    """print() wrapper that replaces un-encodable chars instead of crashing."""
    msg = " ".join(str(a) for a in args)
    safe = msg.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
        sys.stdout.encoding or "utf-8", errors="replace"
    )
    print(safe, **kwargs)

router = APIRouter(dependencies=[Depends(require_session)])

# --- CELU01 is the only MyAdmin account we pull device data for ---------------
MYADMIN_ACCOUNT = "CELU01"

# --- Disk persistence paths ---------------------------------------------------
# _DATA_DIR resolves to GEOBRIDGE_DATA_DIR env var (set by Electron to
# %APPDATA%\GeoBridge on Windows) so that user-written JSON files survive
# application reinstalls.  Falls back to _HERE in dev / unpackaged mode.
# See _data_dir.py for the migration logic that copies existing files on first run.
from ._data_dir import _DATA_DIR, _HERE

# S3 sync — push shared files to S3 after every local save
try:
    from geotab.s3_sync import upload_file_async as _s3_push
except Exception:
    def _s3_push(filename: str) -> None:  # type: ignore
        pass

QB_DATA_FILE           = os.path.join(_DATA_DIR, "qb_customers.json")
QB_ITEMS_FILE          = os.path.join(_DATA_DIR, "qb_items.json")
OVERRIDES_FILE         = os.path.join(_DATA_DIR, "billing_overrides.json")
BILLING_TYPE_OVERRIDES_FILE = os.path.join(_DATA_DIR, "billing_type_overrides.json")
BILLING_DATE_OVERRIDES_FILE      = os.path.join(_DATA_DIR, "billing_date_overrides.json")
FIRST_CONNECT_OVERRIDES_FILE     = os.path.join(_DATA_DIR, "first_connect_date_overrides.json")
BILLING_FREQUENCY_FILE           = os.path.join(_DATA_DIR, "billing_frequency_overrides.json")
SYNC_CACHE_FILE        = os.path.join(_DATA_DIR, "myadmin_cache.json")   # persisted between restarts
CONTRACT_CHECKPOINT_FILE = os.path.join(_DATA_DIR, "contract_checkpoint.json")  # sliding-window resume point

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
    # Mirror to S3 in a background thread — never blocks the response.
    # LOCAL_ONLY files (cache, checkpoint, history) are silently skipped
    # inside upload_file_async itself.
    _s3_push(os.path.basename(path))

# --- In-memory stores -- pre-loaded from disk on startup ----------------------
billing_overrides:       Dict[str, str]  = _load_json(OVERRIDES_FILE, {})
billing_type_overrides:  Dict[str, str]  = {
    k: v for k, v in _load_json(BILLING_TYPE_OVERRIDES_FILE, {}).items()
    if not k.startswith("_")   # skip _comment keys
}
# Manual billing start date overrides: {"SERIAL": "YYYY-MM-DD"}
# Written by POST /api/customers/device/{serial}/billing-date
billing_date_overrides:  Dict[str, str]  = _load_json(BILLING_DATE_OVERRIDES_FILE, {})
# Manual first connect date overrides: {"SERIAL": "YYYY-MM-DD"}
# Written by POST /api/customers/device/{serial}/first-connect-date
# When set, this overrides the MyAdmin firstDeviceActivationDate for invoice proration.
first_connect_date_overrides: Dict[str, str] = _load_json(FIRST_CONNECT_OVERRIDES_FILE, {})
# Billing frequency overrides: {normalize(customerName): {"billingFrequency": str, "billingStartMonth": str|None}}
# Written by POST /api/customers/{account_id}/billing-frequency
# Customers marked with a frequency are shown differently in Reconciliation:
#   - on their billing months  -> shown as No QB Data (amber, alert)
#   - on non-billing months    -> shown as Periodic (teal, suppressed)
# billingStartMonth is "YYYY-MM" anchoring the cycle (e.g. "2024-03" for Quarterly = Mar/Jun/Sep/Dec)
def _load_billing_frequency_overrides() -> Dict[str, dict]:
    raw = _load_json(BILLING_FREQUENCY_FILE, {})
    migrated = {}
    for k, v in raw.items():
        if isinstance(v, str):
            # Migrate old format: plain string -> dict
            migrated[k] = {"billingFrequency": v, "billingStartMonth": None}
        elif isinstance(v, dict):
            migrated[k] = v
    return migrated
billing_frequency_overrides: Dict[str, dict] = _load_billing_frequency_overrides()
qb_customers:       Dict[str, dict] = _load_json(QB_DATA_FILE, {})
# List of {skuKey, fullPath, defaultPrice, desc, isActive} — populated by
# POST /api/customers/refresh-from-qb (live QBFC pull) and persisted to
# QB_ITEMS_FILE so the preflight/report check has data across restarts
# without requiring QuickBooks to be open. There is no CSV-import fallback
# for items (unlike customers) — QBFC live-refresh is the only source.
qb_items:           List[dict]      = _load_json(QB_ITEMS_FILE, [])
name_to_company_id: Dict[str, str]  = {}   # normalize(name) -> companyId, built on sync

# --- MyAdmin sync cache -------------------------------------------------------
CACHE_TTL_HOURS = 3              # Contracts auto-expire after 3 h; background task refreshes silently
DEVICE_DB_REFRESH_MINUTES = 30   # Background Step-1 (device DBs only) refresh interval
WINDOW_SIZE = 4                  # Sliding-window: pages fetched concurrently during Step 2 (legacy v2 fallback)

# --- Step 2 V3 migration (GetDeviceContracts) ---------------------------------
# V3 caps perPage at 100 (confirmed live 2026-09) vs. v2's 1000/page, but pages
# are addressed by NUMBER instead of a chained cursor, and page 1's response
# tells us the exact `total` record count up front. That means we can fetch
# many pages CONCURRENTLY instead of v2's forced one-page-at-a-time cursor
# walk -- this is the actual speed win, not a bigger page size.
#
# CONTRACTS_V3_PAGE_SIZE : Confirmed server-side cap is 100; requesting more
#                          just gets silently capped, so there's no benefit
#                          to asking for less, but also no way to ask for more.
# CONTRACTS_V3_CONCURRENCY : How many pages are in flight at once. Release
#                          notes (June 2025) mention a 1,250-requests-per-
#                          15-min limit specifically named for
#                          GetDeviceContracts(ByPage) -- unclear whether that
#                          still applies to v3, but we stay conservative here
#                          rather than risk 429s turning a 2-minute sync into
#                          a rate-limited multi-hour one. Easy to raise later
#                          once we've watched a few real syncs' timing/logs.
CONTRACTS_V3_PAGE_SIZE    = 100
CONTRACTS_V3_CONCURRENCY  = 8
CONTRACTS_V3_MAX_RETRIES  = 3     # per-page retry count on transient/429 errors before giving up on v3 entirely
_sync_cache: Dict = _load_json(SYNC_CACHE_FILE, {})

# --- Sync lock -- prevents concurrent fetches when multiple requests arrive ---
_sync_lock = asyncio.Lock()

_qb_loaded = bool(qb_customers)
print(f"[customers] QB data: {len(qb_customers)} customers loaded from disk"
      if _qb_loaded else "[customers] QB data: no saved file -- import a CSV to populate")
print(f"[customers] QB items: {len(qb_items)} items loaded from disk"
      if qb_items else "[customers] QB items: no saved file -- use Refresh from QuickBooks to populate")
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
    "sourcewell":                   "Charge Upon Activation",
    "hanover":                      "Hanover",
    "hanover deal":                 "Hanover",
    "han-cs":                       "Han-CS",
    "hancs":                        "Han-CS",
    "check before sending":         "Check Before Sending",
    "reseller":                     "Reseller",
    "in collections":               "In Collections",
    "collections":                  "In Collections",
    "terminated":                   "Terminated",
    "trial":                        "Trial",
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
    #   1. Manual override (billing_overrides.json)  → source = "override"
    #   2. QB Job Type (from qb_customers lookup)    → source = "qb"
    #   3. Han-CS suffix default                     → source = "han-cs"
    #   4. Fall back to 'Unknown'                    → source = "unknown"
    if billing_overrides.get(cid):
        billing_type        = billing_overrides[cid]
        billing_type_source = "override"
    elif qb.get("billingType"):
        billing_type        = qb["billingType"]
        billing_type_source = "qb"
    elif is_han_cs_customer:
        billing_type        = "Han-CS"
        billing_type_source = "han-cs"
    else:
        billing_type        = "Unknown"
        billing_type_source = "unknown"

    return {
        "id":                cid,
        "name":              display_name,
        "accountNo":         qb.get("accountNo") or "",
        "billingType":       billing_type,
        "billingTypeSource": billing_type_source,
        "billingFrequency":  (billing_frequency_overrides.get(normalize(qb_lookup_name)) or {}).get("billingFrequency") or "",
        "billingStartMonth": (billing_frequency_overrides.get(normalize(qb_lookup_name)) or {}).get("billingStartMonth") or None,
        "primaryDatabase":   db_name,
        "deviceCount":       customer.get("activeDevices") or 0,
        "terms":             qb.get("terms") or "",
        "balance":           float(qb.get("balance") or 0),
        "hasQbData":         bool(qb),
        "email":             customer.get("email") or "",
        "phone":             customer.get("phone") or "",
        "address":           customer.get("address") or "",
    }


async def _fetch_contracts_v3() -> List[dict]:
    """
    Fetch ALL device contracts via the new GetDeviceContracts V3 endpoint,
    using numbered-page pagination fetched CONCURRENTLY (bounded by
    CONTRACTS_V3_CONCURRENCY) instead of v2's forced one-at-a-time cursor
    walk. Raises on any unrecoverable error so the caller can fall back to
    the legacy v2 cursor loop (_fetch_contracts_v2_fallback).

    Live-verified response shape (2026-09) is IDENTICAL to v2's
    GetDeviceContractsByPage for all fields this app reads (device,
    userContact.userCompany, isTerminated, etc.) -- only the pagination
    envelope differs. See diag_v3_contracts.py for the probe that confirmed
    this.
    """
    base_params = {
        "apiKey":     session_store["user_id"],
        "sessionId":  session_store["session_id"],
        "forAccount": MYADMIN_ACCOUNT,
    }

    STEP2_START_PCT = 20
    STEP2_END_PCT   = 75
    STEP2_PCT_RANGE = STEP2_END_PCT - STEP2_START_PCT

    async def fetch_page_raw(page: int) -> dict:
        """Fetch a single page and return the FULL response dict (including
        the `pagination` envelope), retrying transient errors (incl. 429) up
        to CONTRACTS_V3_MAX_RETRIES times with exponential backoff. Raises
        the last exception if all retries are exhausted -- the caller
        (fetch_page / the concurrent gather below) treats that as "v3 is
        unhealthy, abort and fall back to v2"."""
        last_exc = None
        for attempt in range(CONTRACTS_V3_MAX_RETRIES):
            try:
                result = await myadmin_call_v3(
                    "GetDeviceContracts",
                    base_params,
                    {"page": page, "perPage": CONTRACTS_V3_PAGE_SIZE},
                    timeout=60.0,
                )
                if "error" in result:
                    raise RuntimeError(f"MyAdmin v3 error on page {page}: {result['error']}")
                return result
            except Exception as e:
                last_exc = e
                # Back off a bit longer on what looks like a rate-limit (429)
                # than on a generic transient error.
                is_429 = "429" in str(e)
                wait = (2.0 if is_429 else 0.5) * (attempt + 1)
                print(f"[sync] v3 page {page} attempt {attempt+1}/{CONTRACTS_V3_MAX_RETRIES} "
                      f"failed ({e}); retrying in {wait:.1f}s..." if attempt + 1 < CONTRACTS_V3_MAX_RETRIES
                      else f"[sync] v3 page {page} failed after {CONTRACTS_V3_MAX_RETRIES} attempts: {e}")
                if attempt + 1 < CONTRACTS_V3_MAX_RETRIES:
                    await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]

    async def fetch_page(page: int) -> list:
        """Convenience wrapper: just the record list for a page."""
        result = await fetch_page_raw(page)
        return result.get("result") or []

    # -- Page 1 first (sequentially) -- gives us both the first batch of
    # records AND the true total record count, in a single request. --------
    _set_progress(
        step="step2",
        step_label="Step 2/2 -- Fetching device contracts (v3)...",
        pct=STEP2_START_PCT,
        records=0,
        message="Requesting page 1 to determine total contract count...",
    )
    print("[sync] Step 2 (v3): fetching page 1 to learn total count...")
    result1 = await fetch_page_raw(1)
    page1 = result1.get("result") or []
    total = int((result1.get("pagination") or {}).get("total") or 0)
    per_page = CONTRACTS_V3_PAGE_SIZE
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    print(f"[sync] Step 2 (v3): total={total:,} contracts across {total_pages:,} pages "
          f"(perPage={per_page}, concurrency={CONTRACTS_V3_CONCURRENCY})")

    all_contracts: List[dict] = list(page1)
    completed = 1

    _set_progress(
        step="step2",
        step_label="Step 2/2 -- Fetching device contracts (v3)...",
        page=1,
        total_pages_est=total_pages,
        records=len(all_contracts),
        pct=STEP2_START_PCT,
        message=f"Page 1/{total_pages:,} -- {len(all_contracts):,} of {total:,} contracts so far...",
    )

    if total_pages <= 1:
        return all_contracts

    # -- Remaining pages fetched CONCURRENTLY, bounded by a semaphore --------
    sem = asyncio.Semaphore(CONTRACTS_V3_CONCURRENCY)

    async def bounded_fetch(page: int):
        async with sem:
            batch = await fetch_page(page)
        return page, batch

    tasks = [asyncio.create_task(bounded_fetch(p)) for p in range(2, total_pages + 1)]

    results_by_page: Dict[int, list] = {1: page1}
    try:
        for coro in asyncio.as_completed(tasks):
            page, batch = await coro
            results_by_page[page] = batch
            completed += 1
            step2_fraction = min(completed / total_pages, 0.99)
            current_pct = int(STEP2_START_PCT + step2_fraction * STEP2_PCT_RANGE)
            records_so_far = sum(len(v) for v in results_by_page.values())
            _set_progress(
                step="step2",
                step_label=f"Step 2/2 -- Fetching contracts (v3, {completed}/{total_pages} pages)...",
                page=completed,
                total_pages_est=total_pages,
                records=records_so_far,
                pct=current_pct,
                message=f"Page {completed}/{total_pages:,} -- {records_so_far:,} of {total:,} contracts so far...",
            )
            # Periodic checkpoint (every ~10 completed pages) in case of crash --
            # resuming a partial v3 fetch isn't implemented yet (concurrent
            # fetch completes in a couple minutes even from scratch), but saving
            # what we have lets a hard crash at least fall back to a partial
            # cache rather than losing everything silently.
            if completed % 10 == 0:
                try:
                    _save_json(CONTRACT_CHECKPOINT_FILE, {
                        "v3_partial": True,
                        "completed_pages": completed,
                        "total_pages": total_pages,
                        "contracts": [c for v in results_by_page.values() for c in v],
                    })
                except Exception as ckpt_err:
                    print(f"[sync] v3 checkpoint write failed (non-fatal): {ckpt_err}")
    except Exception:
        # A page exhausted its retries and raised -- cancel any still-running
        # tasks immediately rather than letting them keep hammering MyAdmin
        # in the background while we fall back to the v2 cursor loop.
        for t in tasks:
            if not t.done():
                t.cancel()
        raise

    # Flatten in page order for determinism (order doesn't matter functionally,
    # but makes logs/debugging easier to reason about).
    all_contracts = [
        c for page in sorted(results_by_page.keys())
        for c in results_by_page[page]
    ]
    return all_contracts


async def _fetch_contracts_v2_fallback(all_device_dbs: List[dict]) -> List[dict]:
    """
    Legacy Step 2 fetch via GetDeviceContractsByPage (v2), using its
    cursor-chained `nextId` pagination -- necessarily sequential, one page
    at a time. Kept as a safety-net fallback for when the new V3 endpoint
    (_fetch_contracts_v3) is unavailable, errors out, or gets rate-limited,
    so a bad night on Geotab's side never breaks a sync outright.
    """
    print("[sync] Step 2 (v2 fallback): fetching device contracts via GetDeviceContractsByPage...")

    # ── Option 3: resume from checkpoint ──────────────────────────────────
    # If a previous sync was interrupted mid-way, a checkpoint file records
    # the last confirmed cursor and contracts collected so far.  The next
    # sync resumes from that point instead of restarting from page 1.
    # The checkpoint is deleted when Step 2 completes successfully.
    ckpt = _load_json(CONTRACT_CHECKPOINT_FILE, {})
    if ckpt.get("next_id") and ckpt.get("contracts") and not ckpt.get("v3_partial"):
        all_contracts = ckpt["contracts"]
        next_id       = ckpt["next_id"]
        # page_num is only used for progress-bar estimation, so an
        # approximate fallback (contracts so far / a nominal 1000-per-page
        # guess) is fine here even if the server's actual page size differs.
        page_num      = ckpt.get("page_num", len(all_contracts) // 1000)
        print(f"[sync] Step 2 (v2 fallback): resuming from checkpoint — "
              f"{len(all_contracts):,} contracts already fetched, nextId={next_id}")
    else:
        all_contracts = []
        next_id       = 0
        page_num      = 0

    # Seeds the "biggest page seen" tracker used below to detect the
    # final (partial) page in a size-agnostic way — see its use in the
    # loop for why this replaces the old hardcoded 1000 check.
    _max_page_size_seen = 1

    STEP2_START_PCT = 20
    STEP2_END_PCT   = 75
    STEP2_PCT_RANGE = STEP2_END_PCT - STEP2_START_PCT
    # Dynamic estimate: starts at the larger of (a) current page + 5 or
    # (b) 50, and is updated inside the loop so it always stays exactly
    # 5 pages ahead of reality — preventing the old hard-coded 120-page
    # floor from pinning the bar far below 100% on a ~103-page sync.
    EST_PAGES       = max(50, page_num + 5)

    _set_progress(
        step="step2",
        step_label="Step 2/2 -- Fetching device contracts (v2 fallback)...",
        pct=STEP2_START_PCT,
        records=len(all_contracts),
        message="Starting contract fetch (v2 fallback)..."
                + (f" Resuming from page ~{page_num}." if page_num else ""),
    )

    while True:
        page_num += 1
        # Update the rolling estimate every page (stays 5 ahead).
        EST_PAGES      = max(EST_PAGES, page_num + 5)
        # Use a square-root curve so the bar accelerates toward 100% rather
        # than saturating: sqrt(page/est) grows more linearly than page/est.
        # Cap at 0.99 so there's always a visible jump when the post-loop
        # _set_progress(pct=75) fires on completion.
        step2_fraction = min((page_num / EST_PAGES) ** 0.5, 0.99)
        current_pct    = int(STEP2_START_PCT + step2_fraction * STEP2_PCT_RANGE)
        _set_progress(
            step="step2",
            step_label=f"Step 2/2 -- Fetching contracts (v2 fallback, page {page_num})...",
            page=page_num,
            total_pages_est=EST_PAGES,
            records=len(all_contracts),
            pct=current_pct,
            message=f"Page {page_num} — {len(all_contracts):,} contracts so far...",
        )
        print(f"[sync] Step 2 (v2 fallback) page {page_num} (nextId={next_id})...")
        result = await myadmin_call(
            "GetDeviceContractsByPage",
            {
                "apiKey":                       session_store["user_id"],
                "sessionId":                    session_store["session_id"],
                "forAccount":                   MYADMIN_ACCOUNT,
                "nextId":                       next_id,
                "includesDeviceConnectionInfo": True,
            },
            timeout=120.0,
        )
        batch = result.get("result") or []
        print(f"[sync] Step 2 (v2 fallback) page {page_num}: {len(batch)} contracts")
        if not batch:
            break
        all_contracts.extend(batch)
        next_id = batch[-1].get("id", 0)

        # Track the largest page size seen so far as a proxy for the
        # server's actual per-page cap (previously hardcoded to the
        # documented 1000-record limit). A batch smaller than that
        # observed max means this was the last (partial) page — this
        # adapts automatically if MyAdmin's cap ever changes (e.g. to
        # 2000) instead of silently assuming 1000 forever. The loop is
        # still safe even if this heuristic is wrong: an incorrectly
        # "non-final" full page just costs one extra round trip that
        # returns empty and exits via the `if not batch` check above.
        _max_page_size_seen = max(_max_page_size_seen, len(batch))
        last_page = (len(batch) < _max_page_size_seen)

        # ── Option 3: checkpoint every 10 pages ───────────────────────────
        # Writes the confirmed cursor + contracts collected so far so a
        # crash/restart can resume mid-sync.  Every 10 pages limits disk
        # I/O while keeping worst-case resume loss to ~10,000 contracts.
        if page_num % 10 == 0 or last_page:
            try:
                _save_json(CONTRACT_CHECKPOINT_FILE, {
                    "next_id":   next_id,
                    "page_num":  page_num,
                    "contracts": all_contracts,
                })
            except Exception as ckpt_err:
                print(f"[sync] Checkpoint write failed (non-fatal): {ckpt_err}")

        if last_page:
            break

    print(f"[sync] Step 2 (v2 fallback) complete: {len(all_contracts)} total contracts")
    return all_contracts


async def _fetch_myadmin_customers(force_refresh: bool = False) -> List[dict]:
    """
    Pull customer + device data from MyAdmin using TWO steps.

    Step 1 -- GetCurrentDeviceDatabases (FAST, ~10-30s total)
        Returns: device serial, DeviceId, DatabaseName (real Geotab DB name)

    Step 2 -- GetDeviceContracts V3 (concurrent, cached CACHE_TTL_HOURS h),
        falling back to legacy v2 GetDeviceContractsByPage (sequential
        cursor walk) if V3 is unavailable/erroring/rate-limited.
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
        # Rely solely on "empty batch = done" rather than a hardcoded page-size
        # check (previously `len(batch) < 1000`). MyAdmin's docs describe 1000
        # as *a* page-size cap, not necessarily a permanent one — this way the
        # loop keeps working correctly (just needing one extra empty-result
        # round trip at the very end) regardless of what size page the server
        # actually returns, with zero code changes needed if that ever moves.
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

    # -- Step 2: GetDeviceContracts (v3, concurrent) w/ v2 cursor fallback ----
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
        print("[sync] Step 2: Fetching device contracts...")

        try:
            all_contracts = await _fetch_contracts_v3()
            print(f"[sync] Step 2 (v3) complete: {len(all_contracts)} total contracts")
        except Exception as v3_err:
            # V3 unavailable/erroring/rate-limited -- fall back to the
            # proven v2 cursor loop rather than failing the whole sync.
            print(f"[sync] Step 2: v3 fetch failed ({v3_err}); "
                  f"falling back to v2 GetDeviceContractsByPage...")
            _set_progress(
                step="step2",
                step_label="Step 2/2 -- v3 unavailable, falling back to v2...",
                pct=20,
                message=f"v3 error: {v3_err} -- retrying with legacy v2 endpoint...",
            )
            all_contracts = await _fetch_contracts_v2_fallback(all_device_dbs)

        _set_progress(
            step="step2",
            step_label="Step 2/2 -- Contracts fetched ok",
            records=len(all_contracts),
            pct=75,
            message=f"{len(all_contracts):,} total contracts fetched",
        )
        _sync_cache["contracts"]        = all_contracts
        _sync_cache["fetched_at"]        = time.time()
        _sync_cache["device_db_records"] = all_device_dbs
        _save_json(SYNC_CACHE_FILE, _sync_cache)

        # Clear checkpoint -- full sync completed successfully.
        try:
            if os.path.exists(CONTRACT_CHECKPOINT_FILE):
                os.remove(CONTRACT_CHECKPOINT_FILE)
        except Exception:
            pass

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
        and not v["customerName"].startswith("* ")   # skip internal / test accounts
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


# --- Shared QB-customer merge logic ------------------------------------------
# Used by BOTH the manual CSV import (POST /api/customers/import-qb) and the
# live QBFC pull (POST /api/customers/refresh-from-qb) so a customer record
# is merged into qb_customers.json identically regardless of which path
# produced it. Each source module is responsible for shaping its raw data
# into this canonical row dict BEFORE calling _merge_qb_customer_row():
#   {name, qbFullName, jobType, accountNo, terms, qbClass, balance (str),
#    billTo1..billTo5}
def _parse_balance(balance_str: str) -> float:
    balance_str = (balance_str or "0").replace("$", "").replace(",", "").strip()
    if balance_str.startswith("(") and balance_str.endswith(")"):
        balance_str = "-" + balance_str[1:-1]
    try:
        return float(balance_str) if balance_str else 0.0
    except ValueError:
        return 0.0


def _merge_qb_customer_row(row: dict, force: bool, source_label: str) -> str:
    """
    Merge one canonical QB-customer row into the module-level qb_customers
    dict, respecting GeoBridge billing-type overrides unless force=True.

    Returns one of: "merged", "skipped" (no name), "protected" (override kept).
    Mutates qb_customers and (if force clears an override) billing_overrides
    in place — callers are responsible for persisting both after the loop.
    """
    name = (row.get("name") or "").strip()
    if not name:
        return "skipped"

    qb_full_name = row.get("qbFullName") or name
    job_type     = row.get("jobType") or ""
    account_no   = row.get("accountNo") or ""
    terms        = row.get("terms") or ""
    qb_class     = row.get("qbClass") or ""
    balance      = _parse_balance(row.get("balance") or "0")

    new_billing_type = map_billing_type(job_type)
    norm_name         = normalize(name)
    company_id        = name_to_company_id.get(norm_name, "")

    has_override = bool(billing_overrides.get(company_id))
    existing      = qb_customers.get(norm_name) or {}
    result        = "merged"
    if has_override and not force:
        preserved_billing = existing.get("billingType") or new_billing_type
        result = "protected"
        _print(f"[{source_label}] Override protected '{name}': "
               f"QB='{new_billing_type}' -> keeping '{preserved_billing}'")
    else:
        preserved_billing = new_billing_type
        if has_override and force:
            del billing_overrides[company_id]
            _print(f"[{source_label}] Force-override: cleared override for "
                   f"'{name}', using QB='{new_billing_type}'")

    qb_customers[norm_name] = {
        "name":        name,
        "qbFullName":  qb_full_name,
        "accountNo":   account_no,
        "billingType": preserved_billing,
        "jobType":     job_type,
        "terms":       terms,
        "qbClass":     qb_class,
        "balance":     balance,
        "billTo1":     (row.get("billTo1") or "").strip(),
        "billTo2":     (row.get("billTo2") or "").strip(),
        "billTo3":     (row.get("billTo3") or "").strip(),
        "billTo4":     (row.get("billTo4") or "").strip(),
        "billTo5":     (row.get("billTo5") or "").strip(),
    }
    return result


# --- POST /api/customers/import-qb --------------------------------------------
@router.post("/customers/import-qb")
async def import_qb_customers(file: UploadFile = File(...), force: bool = Query(False)):
    """Import QuickBooks Customer List CSV.

    ``force=true``  — ignore any existing GeoBridge billing-type overrides and
    always use the QB Job Type value.  Use this when you have just updated Job
    Types in QB and want those values to win everywhere, even for customers that
    were previously edited in GeoBridge.

    Default (``force=false``) — preserves manually-set GeoBridge overrides so
    that a routine re-import never silently overwrites your manual edits.

    NOTE: This manual CSV path is kept as a fallback / offline option now that
    POST /api/customers/refresh-from-qb can pull the same data live via QBFC.
    Both paths share the exact same merge logic (_merge_qb_customer_row) so a
    customer ends up identical in qb_customers.json regardless of which was
    used.
    """
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

        # QB exports sub-jobs in "Parent:Child" format, e.g.:
        #   "City of Greenville, NC - Fleet Services:City of Greenville - Building and Grounds"
        # MyAdmin only knows the child portion ("City of Greenville - Building and Grounds").
        # Strip everything up to and including the last colon so the lookup key
        # matches what MyAdmin stores.  We keep the full raw name for reference.
        qb_full_name = name   # original "Parent:Child" string (stored for reference)
        if ":" in name:
            name = name.rsplit(":", 1)[-1].strip()

        canonical_row = {
            "name":        name,
            "qbFullName":  qb_full_name,
            "jobType":     row.get("Job Type") or row.get("Customer Type") or row.get("Type") or "",
            "accountNo":   (
                row.get("Account No.") or row.get("Account Number")
                or row.get("Account #") or row.get("Acct No") or ""
            ),
            "terms":       row.get("Terms") or row.get("Payment Terms") or "",
            "qbClass":     row.get("Class") or row.get("QB Class") or "",
            "balance":     (
                row.get("Balance Total") or row.get("Balance")
                or row.get("Current Balance") or "0"
            ),
            # QB exports address as free-form lines in columns "Bill to 1"–"Bill to 5".
            # Store all five as-is; the PDF renderer skips line 1 when it
            # duplicates the customer name (QB always repeats it there).
            "billTo1":     row.get("Bill to 1", ""),
            "billTo2":     row.get("Bill to 2", ""),
            "billTo3":     row.get("Bill to 3", ""),
            "billTo4":     row.get("Bill to 4", ""),
            "billTo5":     row.get("Bill to 5", ""),
        }

        result = _merge_qb_customer_row(canonical_row, force, "import-qb")
        if result == "protected":
            protected += 1
            imported += 1
        elif result == "merged":
            imported += 1

    _save_json(QB_DATA_FILE, qb_customers)
    # If force mode cleared any billing overrides, persist the updated dict
    if force:
        _save_json(OVERRIDES_FILE, billing_overrides)
    # Persist the column list so the debug endpoint can report it later
    _save_json(os.path.join(_DATA_DIR, "qb_last_import_columns.json"), csv_columns)
    print(f"[import-qb] Saved {len(qb_customers)} QB customers to {QB_DATA_FILE}")

    msg = f"{imported} customers imported, {skipped} skipped"
    if protected:
        msg += f", {protected} GeoBridge billing overrides preserved"
    if force and imported > 0:
        msg += f" (force mode: QB values applied to all customers)"

    return {
        "success":    True,
        "message":    msg,
        "imported":   imported,
        "skipped":    skipped,
        "protected":  protected,
        "total":      len(qb_customers),
        "csvColumns": csv_columns,   # actual column names seen in this CSV — useful for debugging address mapping
    }


# --- POST /api/customers/refresh-from-qb --------------------------------------
@router.post("/customers/refresh-from-qb")
async def refresh_customers_from_qb(force: bool = Query(False)):
    """
    Live-pull the full Customer list AND Item list directly from
    QuickBooks Desktop via QBFC (COM automation).

    Customers are merged into qb_customers.json — the same cache
    POST /api/customers/import-qb populates from a manual CSV upload.
    Items are a straight replace into qb_items.json (there's no manual
    CSV/override path for items to protect, unlike customers) and feed
    the "no-QB-item-match" bucket of the preflight/report check.

    This is the preferred path going forward (confirmed via
    qb_test_connection.py that QBFC exposes Job Type, Terms, Class, and
    Account Number identically to the CSV export). The CSV import endpoint
    remains available as a manual fallback for customers only.

    WINDOWS ONLY — requires QuickBooks Desktop open locally with the
    company file loaded, and pywin32 installed. Returns HTTP 502 with a
    clear message if QB isn't reachable (never a raw 500).

    ``force`` behaves identically to import-qb: true clears any existing
    GeoBridge billing-type override so QB's Job Type wins; default false
    preserves manual GeoBridge edits. Item refresh is unaffected by
    ``force`` since there's nothing to protect on the item side.

    If the customer pull succeeds but the item pull fails (e.g. a
    transient QB error), the customer merge is still saved and reported —
    the item failure is surfaced as a non-fatal warning in the response
    message rather than rolling back the whole request.
    """
    global qb_customers, qb_items

    from . import qb_connection
    try:
        qb_rows = qb_connection.fetch_customers_from_qb()
    except qb_connection.QBConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if not qb_rows:
        raise HTTPException(
            status_code=502,
            detail="QuickBooks returned zero customers — check that the "
                   "correct company file is open."
        )

    merged    = 0
    skipped   = 0
    protected = 0

    for row in qb_rows:
        result = _merge_qb_customer_row(row, force, "refresh-from-qb")
        if result == "skipped":
            skipped += 1
        elif result == "protected":
            protected += 1
            merged += 1
        else:
            merged += 1

    _save_json(QB_DATA_FILE, qb_customers)
    if force:
        _save_json(OVERRIDES_FILE, billing_overrides)
    print(f"[refresh-from-qb] Saved {len(qb_customers)} QB customers to {QB_DATA_FILE}")

    msg = f"{merged} customers refreshed live from QuickBooks"
    if skipped:
        msg += f", {skipped} skipped (no name)"
    if protected:
        msg += f", {protected} GeoBridge billing overrides preserved"
    if force and merged > 0:
        msg += " (force mode: QB values applied to all customers)"

    items_count = None
    try:
        qb_item_rows = qb_connection.fetch_items_from_qb()
        qb_items = qb_item_rows
        _save_json(QB_ITEMS_FILE, qb_items)
        items_count = len(qb_items)
        print(f"[refresh-from-qb] Saved {items_count} QB items to {QB_ITEMS_FILE}")
        msg += f"; {items_count} items refreshed"
    except qb_connection.QBConnectionError as exc:
        print(f"[refresh-from-qb] Item refresh failed (customers still saved): {exc}")
        msg += f"; item refresh failed ({exc})"

    return {
        "success":     True,
        "message":     msg,
        "merged":      merged,
        "skipped":     skipped,
        "protected":   protected,
        "total":       len(qb_customers),
        "itemsLoaded": items_count if items_count is not None else len(qb_items),
    }





# --- GET /api/debug/qb-columns — show CSV columns from last QB import ---------
@router.get("/debug/qb-columns")
async def debug_qb_columns():
    """Return the column headers that were present in the most recently imported QB CSV."""
    cols = _load_json(os.path.join(_DATA_DIR, "qb_last_import_columns.json"), None)
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
            _serial_key    = _serial.strip().upper()
            _override_date = billing_date_overrides.get(_serial_key)

            # Manual first-connect-date override: user can correct a missing/wrong
            # firstDeviceActivationDate from MyAdmin (e.g. sync ran before first connect).
            _fcd_override  = first_connect_date_overrides.get(_serial_key)

            # firstConnectDate shown in UI: override > API field
            _api_fcd            = _date(d.get("firstDeviceActivationDate") or "")
            _display_fcd        = _fcd_override or _api_fcd

            # Display date: fcd_override / api_fcd > bsd_override > billingStartDate > startDate
            _api_start_date = _date(
                d.get("firstDeviceActivationDate")
                or d.get("billingStartDate")
                or d.get("startDate")
                or ""
            )
            _display_start_date = _fcd_override or _override_date or _api_start_date

            normalized.append({
                "serialNumber":           _serial,
                "deviceType":             (device.get("deviceType") or {}).get("name") or "",
                "activeBillingPlan":      active_billing_plan,
                "ratePlanCode":           rate_plan_code,
                "database":               db_name,
                "status":                 "Never Activated" if _is_never_activated else "Active",
                "contractStartDate":      _display_start_date,
                "hasDateOverride":        _override_date is not None,
                "firstConnectDate":       _display_fcd,
                "hasFirstConnectOverride": _fcd_override is not None,
                "contractEndDate":        _date(d.get("endDate") or ""),
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


# --- POST /api/customers/billing-type/bulk -----------------------------------
# Must be defined BEFORE the wildcard /{account_id} route so FastAPI matches
# the literal path first.
class BillingTypeBulkItem(BaseModel):
    id:           str
    billing_type: str

class BillingTypeBulkUpdate(BaseModel):
    updates: List[BillingTypeBulkItem]

@router.post("/customers/billing-type/bulk")
async def bulk_set_billing_type(body: BillingTypeBulkUpdate):
    """Set billing type for multiple customers in one request.

    Each item in ``updates`` is ``{id, billing_type}``.  Invalid billing types
    are rejected for the whole batch (atomic validation).  Valid updates are
    written to billing_overrides.json in a single file save.
    """
    valid = {
        "Standard", "Charge Upon Activation", "Hanover", "Han-CS",
        "Check Before Sending", "Reseller", "In Collections",
        "Terminated", "Unknown", "Trial",
    }
    # Validate all before writing any
    for item in body.updates:
        if item.billing_type not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid billing type '{item.billing_type}' for id '{item.id}'"
            )
    for item in body.updates:
        billing_overrides[item.id] = item.billing_type
    _save_json(OVERRIDES_FILE, billing_overrides)
    return {
        "success": True,
        "updated": len(body.updates),
    }


# --- POST /api/customers/{account_id}/billing-type ---------------------------
class BillingTypeUpdate(BaseModel):
    billing_type: str

@router.post("/customers/{account_id}/billing-type")
async def set_billing_type(account_id: str, body: BillingTypeUpdate):
    valid = [
        "Standard", "Charge Upon Activation", "Hanover", "Han-CS",
        "Check Before Sending",
        "Reseller", "In Collections", "Terminated", "Unknown", "Trial",
    ]
    if body.billing_type not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid billing type: {body.billing_type}")
    billing_overrides[account_id] = body.billing_type
    _save_json(OVERRIDES_FILE, billing_overrides)
    return {"success": True, "customerId": account_id, "billingType": body.billing_type}


# --- DELETE /api/customers/{account_id}/billing-type -------------------------
# Clears the manual override so the QB Job Type wins on next lookup.
@router.delete("/customers/{account_id}/billing-type")
async def clear_billing_type(account_id: str):
    """Remove the manual billing-type override for this account.

    After clearing, ``enrich_customer()`` will fall back to the QB Job Type
    (or Han-CS / Unknown) — exactly as if the user had never set it manually.
    """
    had_override = account_id in billing_overrides
    if had_override:
        del billing_overrides[account_id]
        _save_json(OVERRIDES_FILE, billing_overrides)
    return {
        "success":     True,
        "customerId":  account_id,
        "hadOverride": had_override,
    }


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


# =============================================================================
#  Manual first-connect-date overrides  (per-device)
# =============================================================================

class FirstConnectDateOverride(BaseModel):
    firstConnectDate: str   # "YYYY-MM-DD"


@router.post("/customers/device/{serial}/first-connect-date")
async def set_device_first_connect_date(serial: str, body: FirstConnectDateOverride):
    """
    Manually set (or replace) the First Connect Date for a device serial.
    Stored in first_connect_date_overrides.json as {"SERIAL": "YYYY-MM-DD"}.

    This date takes priority over MyAdmin's firstDeviceActivationDate in invoice
    generation (Rule 3 fires — uses fcd as activation date).  Use this when the
    MyAdmin sync ran before the device had its first connection, leaving
    firstDeviceActivationDate as 0001-01-01 (null), but the true first-connect
    date is known from the MyAdmin UI.
    """
    from datetime import date as _date_cls
    try:
        _date_cls.fromisoformat(body.firstConnectDate)
    except ValueError:
        raise HTTPException(status_code=400, detail="firstConnectDate must be YYYY-MM-DD")

    key = serial.strip().upper()
    first_connect_date_overrides[key] = body.firstConnectDate
    _save_json(FIRST_CONNECT_OVERRIDES_FILE, first_connect_date_overrides)
    return {"success": True, "serial": key, "firstConnectDate": body.firstConnectDate}


@router.delete("/customers/device/{serial}/first-connect-date")
async def delete_device_first_connect_date(serial: str):
    """
    Remove the manual first-connect-date override for a device serial.
    After deletion the invoice engine falls back to MyAdmin's firstDeviceActivationDate,
    then billingStartDate.
    """
    key = serial.strip().upper()
    if key not in first_connect_date_overrides:
        raise HTTPException(status_code=404, detail=f"No first-connect-date override found for serial '{serial}'")

    del first_connect_date_overrides[key]
    _save_json(FIRST_CONNECT_OVERRIDES_FILE, first_connect_date_overrides)
    return {"success": True, "serial": key, "cleared": True}


@router.get("/customers/device/{serial}/first-connect-date")
async def get_device_first_connect_date(serial: str):
    """Return the current manual first-connect-date override for a serial (if any)."""
    key = serial.strip().upper()
    override = first_connect_date_overrides.get(key)
    return {
        "serial":           key,
        "hasOverride":      override is not None,
        "firstConnectDate": override,
    }


# =============================================================================
#  Billing frequency overrides  (per-customer)
# =============================================================================

VALID_BILLING_FREQUENCIES = {"Annual", "Semi-Annual", "Quarterly"}

class BillingFrequencyUpdate(BaseModel):
    billingFrequency: str             # "Annual" | "Semi-Annual" | "Quarterly"
    billingStartMonth: Optional[str] = None  # "YYYY-MM" anchor for cycle, e.g. "2024-03"


@router.post("/customers/{account_id}/billing-frequency")
async def set_billing_frequency(account_id: str, body: BillingFrequencyUpdate):
    """
    Mark a customer as Annual, Semi-Annual, or Quarterly.

    These customers are only invoiced 1, 2, or 4 times a year, so on months
    where they have no QB invoice they should not appear as 'No QB Data' in
    Reconciliation — they just aren't billed that month.

    Stored in billing_frequency_overrides.json keyed by normalize(customerName).
    The account_id is resolved to a customer name from the sync cache.
    """
    if body.billingFrequency not in VALID_BILLING_FREQUENCIES and body.billingFrequency != "":
        raise HTTPException(
            status_code=400,
            detail=f"billingFrequency must be one of: {', '.join(sorted(VALID_BILLING_FREQUENCIES))} (or empty to clear)"
        )

    # Resolve account_id -> normalize(customerName) using the sync cache
    contracts: List[dict] = _sync_cache.get("contracts") or []
    # Find any contract for this company ID
    company_name = None
    for c in contracts:
        cid = str(((c.get("userContact") or {}).get("userCompany") or {}).get("id") or "")
        if cid == str(account_id):
            company_name = ((c.get("userContact") or {}).get("userCompany") or {}).get("name") or ""
            break

    if not company_name:
        # No contract found — use account_id itself as the key (fallback)
        norm_key = normalize(account_id)
    else:
        # Strip sub-account / Han-CS suffixes so sub-accounts share the parent key
        qb_lookup = _strip_han_cs(_strip_sub_account_suffix(company_name))
        norm_key = normalize(qb_lookup)

    if body.billingFrequency:
        billing_frequency_overrides[norm_key] = {
            "billingFrequency":  body.billingFrequency,
            "billingStartMonth": body.billingStartMonth or None,
        }
    else:
        billing_frequency_overrides.pop(norm_key, None)

    _save_json(BILLING_FREQUENCY_FILE, billing_frequency_overrides)
    return {
        "success":           True,
        "accountId":         account_id,
        "normKey":           norm_key,
        "billingFrequency":  body.billingFrequency or None,
        "billingStartMonth": body.billingStartMonth or None,
    }


@router.delete("/customers/{account_id}/billing-frequency")
async def delete_billing_frequency(account_id: str):
    """Remove the billing frequency override for a customer."""
    contracts: List[dict] = _sync_cache.get("contracts") or []
    company_name = None
    for c in contracts:
        cid = str(((c.get("userContact") or {}).get("userCompany") or {}).get("id") or "")
        if cid == str(account_id):
            company_name = ((c.get("userContact") or {}).get("userCompany") or {}).get("name") or ""
            break

    if not company_name:
        norm_key = normalize(account_id)
    else:
        qb_lookup = _strip_han_cs(_strip_sub_account_suffix(company_name))
        norm_key = normalize(qb_lookup)

    if norm_key not in billing_frequency_overrides:
        raise HTTPException(status_code=404, detail=f"No billing frequency override for account '{account_id}'")

    del billing_frequency_overrides[norm_key]
    _save_json(BILLING_FREQUENCY_FILE, billing_frequency_overrides)
    return {"success": True, "accountId": account_id, "cleared": True}


@router.get("/customers/{account_id}/billing-frequency")
async def get_billing_frequency(account_id: str):
    """Return the billing frequency override for a customer (if any)."""
    contracts: List[dict] = _sync_cache.get("contracts") or []
    company_name = None
    for c in contracts:
        cid = str(((c.get("userContact") or {}).get("userCompany") or {}).get("id") or "")
        if cid == str(account_id):
            company_name = ((c.get("userContact") or {}).get("userCompany") or {}).get("name") or ""
            break

    if not company_name:
        norm_key = normalize(account_id)
    else:
        qb_lookup = _strip_han_cs(_strip_sub_account_suffix(company_name))
        norm_key = normalize(qb_lookup)

    rec = billing_frequency_overrides.get(norm_key)
    return {
        "accountId":         account_id,
        "hasOverride":       rec is not None,
        "billingFrequency":  (rec or {}).get("billingFrequency") if rec else None,
        "billingStartMonth": (rec or {}).get("billingStartMonth") if rec else None,
    }
