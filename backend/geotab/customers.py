from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import csv
import io
import json
import os
import sys
import time
from .auth import myadmin_call, session_store

# ─── Windows-safe print (avoids CP1252 UnicodeEncodeError on arrow chars) ─────
def _print(*args, **kwargs):
    """print() wrapper that replaces un-encodable chars instead of crashing."""
    msg = " ".join(str(a) for a in args)
    safe = msg.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
        sys.stdout.encoding or "utf-8", errors="replace"
    )
    print(safe, **kwargs)

router = APIRouter()

# ─── CELU01 is the only MyAdmin account we pull device data for ───────────────
MYADMIN_ACCOUNT = "CELU01"

# ─── Disk persistence paths ───────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
QB_DATA_FILE    = os.path.join(_HERE, "qb_customers.json")
OVERRIDES_FILE  = os.path.join(_HERE, "billing_overrides.json")
SYNC_CACHE_FILE = os.path.join(_HERE, "myadmin_cache.json")   # persisted between restarts

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

# ─── In-memory stores — pre-loaded from disk on startup ──────────────────────
billing_overrides:  dict[str, str]  = _load_json(OVERRIDES_FILE, {})
qb_customers:       dict[str, dict] = _load_json(QB_DATA_FILE, {})
qb_items:           list[dict]      = []
name_to_company_id: dict[str, str]  = {}   # normalize(name) -> companyId, built on sync

# ─── MyAdmin sync cache ───────────────────────────────────────────────────────
CACHE_TTL_HOURS = 12
_sync_cache: dict = _load_json(SYNC_CACHE_FILE, {})

# ─── Sync lock — prevents concurrent fetches when multiple requests arrive ───
_sync_lock = asyncio.Lock()

_qb_loaded = bool(qb_customers)
print(f"[customers] QB data: {len(qb_customers)} customers loaded from disk"
      if _qb_loaded else "[customers] QB data: no saved file — import a CSV to populate")
if _sync_cache.get("fetched_at"):
    age_h = (time.time() - _sync_cache["fetched_at"]) / 3600
    print(f"[customers] MyAdmin cache: {len(_sync_cache.get('customers', []))} customers, "
          f"{age_h:.1f}h old (TTL {CACHE_TTL_HOURS}h)")

# ─── Real-time sync progress state ────────────────────────────────────────────
# Updated by _fetch_myadmin_customers(); polled by the SSE endpoint.
_sync_progress: dict = {
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


# ─── Billing type map ─────────────────────────────────────────────────────────
QB_JOB_TYPE_MAP = {
    "standard":                     "Standard",
    "cua":                          "CUA",
    "sourcewell":                   "Sourcewell",
    "hanover":                      "Hanover",
    "han-cs":                       "Han-CS",
    "hancs":                        "Han-CS",
    "charge upon activation":       "Charge Upon Activation",
    "cua - charge upon activation": "Charge Upon Activation",
    "check before sending":         "Check Before Sending",
    "reseller":                     "Reseller",
    "in collections":               "In Collections",
    "collections":                  "In Collections",
    "terminated":                   "Terminated",
}

def normalize(name: str) -> str:
    return name.strip().lower()

def map_billing_type(job_type: str) -> str:
    if not job_type:
        return "Unknown"
    key = normalize(job_type)
    for pattern, billing in QB_JOB_TYPE_MAP.items():
        if pattern in key:
            return billing
    return "Unknown"

def enrich_customer(customer: dict) -> dict:
    """Merge grouped MyAdmin record with QB data and billing overrides."""
    company_id   = str(customer.get("companyId") or "")
    company_name = customer.get("customerName") or ""
    db_name      = customer.get("primaryDatabase") or ""

    qb = qb_customers.get(normalize(company_name)) or {}
    display_name = qb.get("name") or company_name or f"Company {company_id}"
    cid = company_id or normalize(company_name)

    billing_type = (
        billing_overrides.get(cid)
        or qb.get("billingType")
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


async def _fetch_myadmin_customers() -> list[dict]:
    """
    Pull customer + device data from MyAdmin using TWO steps.

    Step 1 — GetCurrentDeviceDatabases (FAST, ~10-30s total)
        Returns: device serial, DeviceId, DatabaseName (real Geotab DB name)

    Step 2 — GetDeviceContractsByPage (SLOW, ~2-5min, but cached 12h)
        Returns: userCompany.name (customer name), isTerminated, device.id

    Progress is emitted to _sync_progress so the SSE endpoint can stream it
    to the frontend in real time.
    """
    # ── Reset / start progress ─────────────────────────────────────────────────
    _set_progress(
        active=True,
        step="step1",
        step_label="Step 1/2 — Fetching device databases…",
        page=0,
        total_pages_est=0,
        records=0,
        pct=2,
        message="Contacting MyAdmin…",
        error="",
    )

    # ── Step 1: GetCurrentDeviceDatabases ────────────────────────────────────
    print("[sync] Step 1: Fetching current device databases (fast)…")
    all_device_dbs = []
    next_id = 0
    page_num = 0
    while True:
        page_num += 1
        _set_progress(
            step="step1",
            step_label=f"Step 1/2 — Fetching device databases (page {page_num})…",
            page=page_num,
            pct=min(2 + page_num * 3, 20),   # grows to ~20% during step 1
            message=f"Device database page {page_num}…",
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
    _set_progress(
        step="step1",
        step_label="Step 1/2 — Device databases fetched ✓",
        pct=20,
        message=f"{len(all_device_dbs):,} device-db records",
    )

    # ── Step 2: GetDeviceContractsByPage ─────────────────────────────────────
    cache_age = time.time() - (_sync_cache.get("fetched_at") or 0)
    cache_ok  = (
        _sync_cache.get("contracts")
        and cache_age < CACHE_TTL_HOURS * 3600
    )

    if cache_ok:
        print(f"[sync] Step 2: Using cached contracts ({cache_age/3600:.1f}h old)")
        all_contracts = _sync_cache["contracts"]
        _set_progress(
            step="step2",
            step_label="Step 2/2 — Using cached contracts ✓",
            pct=75,
            message=f"{len(all_contracts):,} contracts from 12-hour cache",
        )
    else:
        print("[sync] Step 2: Fetching device contracts (slow — customer names)…")
        all_contracts = []
        next_id  = 0
        page_num = 0

        # We'll estimate ~120 pages for CELU01 (100k+ contracts / 1000 per page).
        # The bar runs from 20% → 75% during step 2.
        STEP2_START_PCT = 20
        STEP2_END_PCT   = 75
        STEP2_PCT_RANGE = STEP2_END_PCT - STEP2_START_PCT
        EST_PAGES       = 120   # conservative estimate; recalculates as we go

        _set_progress(
            step="step2",
            step_label="Step 2/2 — Fetching device contracts…",
            pct=STEP2_START_PCT,
            message="Starting contract fetch…",
        )

        while True:
            page_num += 1
            # Progress within step 2: linear up to 95% of the step's range,
            # then clamp — we don't know total pages until the last batch < 1000.
            step2_fraction = min(page_num / max(EST_PAGES, page_num + 1), 0.95)
            current_pct    = int(STEP2_START_PCT + step2_fraction * STEP2_PCT_RANGE)
            _set_progress(
                step="step2",
                step_label=f"Step 2/2 — Fetching contracts (page {page_num})…",
                page=page_num,
                total_pages_est=max(EST_PAGES, page_num),
                records=len(all_contracts),
                pct=current_pct,
                message=f"Page {page_num} · {len(all_contracts):,} contracts so far…",
            )
            print(f"[sync] Step 2 page {page_num} (nextId={next_id})…")
            result = await myadmin_call(
                "GetDeviceContractsByPage",
                {
                    "apiKey":     session_store["user_id"],
                    "sessionId":  session_store["session_id"],
                    "forAccount": MYADMIN_ACCOUNT,
                    "nextId":     next_id,
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
            step_label="Step 2/2 — Contracts fetched ✓",
            records=len(all_contracts),
            pct=75,
            message=f"{len(all_contracts):,} total contracts fetched",
        )
        _sync_cache["contracts"]  = all_contracts
        _sync_cache["fetched_at"] = time.time()
        _save_json(SYNC_CACHE_FILE, _sync_cache)

    # ── Processing: join + group ───────────────────────────────────────────────
    _set_progress(
        step="processing",
        step_label="Processing — Joining device & contract data…",
        pct=80,
        message="Building customer list…",
    )

    device_id_to_company: dict[str, dict] = {}
    for c in all_contracts:
        device   = c.get("device") or {}
        dev_id   = str(device.get("id") or "")
        if not dev_id:
            continue
        uc       = c.get("userContact") or {}
        company  = uc.get("userCompany") or {}
        device_id_to_company[dev_id] = {
            "companyId":   str(company.get("id") or ""),
            "companyName": company.get("name") or "",
            "terminated":  bool(c.get("isTerminated")),
        }

    _set_progress(pct=85, message="Grouping by company…")

    company_map: dict[str, dict] = {}
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

    raw = [
        v for v in company_map.values()
        if v["activeDevices"] > 0
        and not v["customerName"].startswith("* Terminated")
    ]
    _print(f"[sync] {len(all_device_dbs)} device-db records -> "
           f"{len(company_map)} companies -> {len(raw)} with active devices")

    # ── Rebuild name → companyId lookup ─────────────────────────────────────
    global name_to_company_id
    name_to_company_id = {
        normalize(v["customerName"]): v["companyId"]
        for v in raw if v["customerName"]
    }

    # ── Done ─────────────────────────────────────────────────────────────────
    _set_progress(
        active=False,
        step="done",
        step_label="Sync complete ✓",
        pct=100,
        message=f"{len(raw):,} customers loaded",
    )

    return raw


# ─── GET /api/customers/sync-progress  (SSE — MUST be before wildcard route) ─
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
        # ── Phase 1: wait for the sync to start (up to 5 s) ──────────────────
        waited = 0.0
        while not _sync_progress["active"] and waited < 5.0:
            yield f"data: {json.dumps(_sync_progress)}\n\n"
            await asyncio.sleep(0.3)
            waited += 0.3

        if not _sync_progress["active"]:
            # Sync never started — close the stream
            yield f"data: {json.dumps(_sync_progress)}\n\n"
            return

        # ── Phase 2: stream while active ─────────────────────────────────────
        last_json = None
        while _sync_progress["active"] or _sync_progress["step"] not in ("done", "error", ""):
            current = dict(_sync_progress)
            current_json = json.dumps(current)
            if current_json != last_json:
                yield f"data: {current_json}\n\n"
                last_json = current_json
            await asyncio.sleep(0.3)

        # ── Final flush ───────────────────────────────────────────────────────
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


# ─── GET /api/customers ────────────────────────────────────────────────────────
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
        use_cache = (
            not force_refresh
            and _sync_cache.get("raw_customers")
            and cache_age < CACHE_TTL_HOURS * 3600
        )

        if use_cache:
            print(f"[customers] Using cached customer list ({cache_age/3600:.1f}h old)")
            raw = _sync_cache["raw_customers"]
        else:
            # Use a lock so concurrent page-load requests don't all trigger
            # simultaneous full syncs — only the first one fetches, the rest
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
                    raw = await _fetch_myadmin_customers()
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


# ─── GET /api/customers/qb-data/summary ───────────────────────────────────────
@router.get("/customers/qb-data/summary")
async def get_qb_summary():
    if not qb_customers:
        return {
            "customersLoaded":      0,
            "itemsLoaded":          len(qb_items),
            "billingTypeBreakdown": {},
        }
    breakdown: dict[str, int] = {}
    for qb in qb_customers.values():
        bt = qb.get("billingType") or "Unknown"
        breakdown[bt] = breakdown.get(bt, 0) + 1
    return {
        "customersLoaded":      len(qb_customers),
        "itemsLoaded":          len(qb_items),
        "billingTypeBreakdown": breakdown,
    }


# ─── POST /api/customers/import-qb ────────────────────────────────────────────
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
            "balance":     balance,
        }
        imported += 1

    _save_json(QB_DATA_FILE, qb_customers)
    print(f"[import-qb] Saved {len(qb_customers)} QB customers to {QB_DATA_FILE}")

    msg = f"{imported} customers imported, {skipped} skipped"
    if protected:
        msg += f", {protected} GeoBridge billing overrides preserved"

    return {
        "success":   True,
        "message":   msg,
        "imported":  imported,
        "skipped":   skipped,
        "protected": protected,
        "total":     len(qb_customers),
    }


# ─── GET /api/customers/{account_id} ──────────────────────────────────────────
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
                    "apiKey":     session_store["user_id"],
                    "sessionId":  session_store["session_id"],
                    "forAccount": MYADMIN_ACCOUNT,
                    "nextId":     0,
                },
                timeout=120.0,
            )
            all_contracts = result.get("result") or []

        matching = [
            c for c in all_contracts
            if str(((c.get("userContact") or {}).get("userCompany") or {}).get("id") or "") == account_id
        ]

        normalized = []
        for d in matching:
            device = d.get("device") or {}
            normalized.append({
                "serialNumber":      device.get("serialNumber") or "",
                "deviceType":        (device.get("deviceType") or {}).get("name") or "",
                "activeBillingPlan": d.get("productCode") or "",
                "ratePlanCode":      d.get("productCode") or "",
                "database":          str(((d.get("userContact") or {}).get("userCompany") or {}).get("id") or ""),
                "status":            "Terminated" if d.get("isTerminated") else "Active",
                "contractStartDate": d.get("startDate") or "",
                "contractEndDate":   d.get("endDate") or "",
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


# ─── POST /api/customers/{account_id}/billing-type ───────────────────────────
class BillingTypeUpdate(BaseModel):
    billing_type: str

@router.post("/customers/{account_id}/billing-type")
async def set_billing_type(account_id: str, body: BillingTypeUpdate):
    valid = [
        "Standard", "CUA", "Sourcewell", "Hanover", "Han-CS",
        "Charge Upon Activation", "Check Before Sending",
        "Reseller", "In Collections", "Terminated", "Unknown",
    ]
    if body.billing_type not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid billing type: {body.billing_type}")
    billing_overrides[account_id] = body.billing_type
    _save_json(OVERRIDES_FILE, billing_overrides)
    return {"success": True, "customerId": account_id, "billingType": body.billing_type}


# ─── GET /api/customers/{account_id}/devices ─────────────────────────────────
@router.get("/customers/{account_id}/devices")
async def get_customer_devices(account_id: str):
    return await get_customer(account_id)
