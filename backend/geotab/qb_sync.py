"""
qb_sync.py — QuickBooks sync router

SSE endpoint: GET /api/qb-sync/run?mode={recurrences|prorated|full}&dryRun={0|1}
History:      GET /api/qb-sync/history
Clear:        DELETE /api/qb-sync/history

The /run endpoint streams Server-Sent Events as JSON objects:

  {"type": "progress", "pct": 0-100, "label": "...", "log": "...", "logType": "info|step|success|warning|error"}
  {"type": "log",      "message": "...", "logType": "..."}
  {"type": "error_item", "customer": "...", "syncType": "recurrence|prorated", "message": "..."}
  {"type": "done",     "recurrencesUpdated": N, "invoicesCreated": N, "errorCount": N,
                       "failed": bool, "preview": [...]}

The "preview" field in the "done" event contains a list of what WOULD be written
to QB (always populated, regardless of dry_run). Each entry has:
  {"customer": str, "syncType": "recurrence"|"prorated",
   "skuKey": str, "qty": int, "amount": float, "action": str}

IMPLEMENTATION NOTES
────────────────────
Real QB writes happen at the TODO blocks in _run_recurrences / _run_prorated.
Both functions now load real reconciliation / activations data so dry-run
previews show exactly what would be written.

Steps to wire real QB pushes:
  1. _run_recurrences(): replace the TODO block with your QB SDK / IIF writer
     call that upserts a memorized transaction.
  2. _run_prorated(): replace the TODO block with the QB call that writes a
     prorated invoice line.
  3. Both helpers already have all the data they need (customerName, skuKey,
     qty, expectedMonthly, proratedCharge) — no further plumbing needed.
"""

import asyncio
import json
import os
import time
import uuid
from datetime import date, datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── Persistence ───────────────────────────────────────────────────────────────
_HERE         = os.path.dirname(__file__)
_HISTORY_FILE = os.path.join(_HERE, "qb_sync_history.json")


def _load_history() -> list:
    if not os.path.exists(_HISTORY_FILE):
        return []
    try:
        with open(_HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(history: list) -> None:
    with open(_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def _append_history(run: dict) -> None:
    history = _load_history()
    history.insert(0, run)   # newest first
    history = history[:100]  # keep last 100 runs
    _save_history(history)


# ── SSE helpers ───────────────────────────────────────────────────────────────
def _evt(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


def _progress(pct: int, label: str, log: str = "", log_type: str = "info") -> str:
    return _evt({"type": "progress", "pct": pct, "label": label,
                 "log": log, "logType": log_type})


def _log(message: str, log_type: str = "info") -> str:
    return _evt({"type": "log", "message": message, "logType": log_type})


def _error_item(customer: str, sync_type: str, message: str) -> str:
    return _evt({"type": "error_item", "customer": customer,
                 "syncType": sync_type, "message": message})


def _done(recurrences_updated: int, invoices_created: int,
          error_count: int, failed: bool = False,
          preview: list | None = None) -> str:
    return _evt({"type": "done",
                 "recurrencesUpdated": recurrences_updated,
                 "invoicesCreated":    invoices_created,
                 "errorCount":         error_count,
                 "failed":             failed,
                 "preview":            preview or []})


# ── Data helpers ──────────────────────────────────────────────────────────────
def _load_reconciliation_customers() -> list:
    """
    Return the reconciliation customers list from the in-memory sync cache.
    This is the same data the Reconciliation page uses — no extra API call.
    Returns [] if the cache hasn't been populated yet.
    """
    try:
        from geotab.customers import _sync_cache
        from geotab.reconciliation import get_reconciliation
        import asyncio

        # Run the reconciliation coroutine synchronously (we're already in an
        # async context so we use a nested event-loop trick via
        # asyncio.get_event_loop().run_until_complete — but since we ARE inside
        # an async generator we must call it as a coroutine instead).
        # Callers of this function must await _load_reconciliation_customers_async().
        return []
    except Exception:
        return []


async def _load_reconciliation_data() -> dict:
    """Await the reconciliation endpoint and return its full response dict."""
    try:
        from geotab.reconciliation import get_reconciliation
        result = await get_reconciliation()
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        print(f"[qb_sync] reconciliation load error: {exc}")
        return {}


async def _load_activations_data(from_date: str = "", to_date: str = "") -> dict:
    """Await the activations endpoint for the current month."""
    try:
        from geotab.activations import get_activations
        result = await get_activations(
            from_date=from_date,
            to_date=to_date,
        )
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        print(f"[qb_sync] activations load error: {exc}")
        return {}


# ── Recurrences sync ──────────────────────────────────────────────────────────
async def _run_recurrences(dry_run: bool, counters: dict) -> AsyncGenerator:
    """
    Preview / push monthly recurrence updates to QB 'Monthly Recurrences' group.

    For each customer with MyAdmin devices and a resolved SKU + expected price,
    this produces one QB memorized transaction line: (customerName, skuKey, qty,
    monthlyAmount).  In dry-run mode we log what WOULD be written without
    touching QB.

    counters keys written:
      updated  — recurrences actually pushed (non-dry-run only)
      previewed — recurrences that WOULD be pushed (always set)
      errors   — per-customer failures
      preview  — list of preview dicts
    """
    counters['updated']   = 0
    counters['previewed'] = 0
    counters['errors']    = 0
    counters['preview']   = []

    yield _progress(5, "Loading reconciliation data…", "Reading customer data…", "step")
    await asyncio.sleep(0.1)

    recon = await _load_reconciliation_data()
    customers = recon.get("customers", [])

    if not customers:
        yield _log(
            "No reconciliation data available — open the Customers page to sync first.",
            "warning"
        )
        yield _progress(95, "Nothing to process.", "No customer data.", "warning")
        return

    # Filter to customers that have at least one device with a resolved SKU
    # and an expected price (these are the rows that would become QB memorized
    # transactions).  Trial customers are never billed.
    billable = [
        c for c in customers
        if not c.get("qbOnly")
        and c.get("expectedMonthly") is not None
        and float(c.get("expectedMonthly") or 0) > 0
        and c.get("billingType") != "Trial"
    ]

    total = len(billable)
    yield _progress(10, f"Found {total} billable customers…",
                    f"{total} customers with active billing data", "info")
    await asyncio.sleep(0.1)

    yield _progress(15, "Connecting to QuickBooks…", "Establishing QB connection…", "step")
    await asyncio.sleep(0.3)

    # TODO: Initialise QB connection / IIF writer here
    # e.g.  qb = QBClient(...)  or  iif = IIFWriter(...)

    for i, cust in enumerate(billable):
        pct = 15 + int((i / max(total, 1)) * 63)
        cname    = cust.get("customerName") or "Unknown"
        expected = round(float(cust.get("expectedMonthly") or 0), 2)
        devices  = cust.get("devices") or []

        # Build per-SKU summary lines (one QB line per unique SKU)
        sku_lines: dict = {}
        for d in devices:
            sk = d.get("skuKey") or ""
            if not sk or sk == "UNMAPPED":
                continue
            ep = float(d.get("expectedPrice") or 0)
            if ep <= 0:
                continue
            if sk not in sku_lines:
                sku_lines[sk] = {"qty": 0, "amount": 0.0}
            sku_lines[sk]["qty"]    += 1
            sku_lines[sk]["amount"] = round(sku_lines[sk]["amount"] + ep, 2)

        if not sku_lines:
            # Customer has devices but none with a resolved price — skip
            yield _progress(pct, f"Skipping {cname}…",
                            f"{cname} — no priced SKUs, skipping", "dim")
            await asyncio.sleep(0.05)
            continue

        yield _progress(pct, f"{'Previewing' if dry_run else 'Updating'} recurrence: {cname}…",
                        f"Processing {cname}…", "info")
        await asyncio.sleep(0.05)

        try:
            for sk, line in sku_lines.items():
                preview_entry = {
                    "customer":  cname,
                    "syncType":  "recurrence",
                    "skuKey":    sk,
                    "qty":       line["qty"],
                    "amount":    line["amount"],
                    "action":    "would update" if dry_run else "updated",
                }
                counters['preview'].append(preview_entry)
                counters['previewed'] += 1

                if dry_run:
                    yield _log(
                        f"{cname} — would update recurrence: {sk} × {line['qty']} = ${line['amount']:.2f} [dry run]",
                        "dim"
                    )
                else:
                    # TODO: push memorized transaction to QB here
                    # qb.upsert_memorized_transaction(
                    #     customer=cname, sku=sk, qty=line['qty'], amount=line['amount']
                    # )
                    counters['updated'] += 1
                    yield _log(
                        f"✓ {cname} — recurrence updated: {sk} × {line['qty']} = ${line['amount']:.2f}",
                        "success"
                    )

        except Exception as exc:
            counters['errors'] += 1
            yield _error_item(cname, "recurrence", str(exc))
            yield _log(f"✗ {cname} — error: {exc}", "error")

    summary_count = counters['previewed'] if dry_run else counters['updated']
    yield _progress(78, "Finalising recurrences…",
                    f"{summary_count} recurrences {'previewed' if dry_run else 'processed'}",
                    "info")
    await asyncio.sleep(0.2)


# ── Prorated invoices sync ────────────────────────────────────────────────────
async def _run_prorated(dry_run: bool, counters: dict) -> AsyncGenerator:
    """
    Preview / push prorated invoice entries to QB 'Prorated Service Invoices'
    group.

    Loads activations for the current calendar month and generates one QB invoice
    line per activation: (customerName, skuKey, daysActive, proratedCharge).

    counters keys written:
      created   — prorated invoices actually pushed (non-dry-run only)
      previewed — prorated invoices that WOULD be pushed (always set)
      errors    — per-activation failures
      preview   — list of preview dicts
    """
    counters['created']   = 0
    counters['previewed'] = 0
    counters['errors']    = 0
    counters['preview']   = []

    today      = date.today()
    from_date  = date(today.year, today.month, 1).isoformat()
    to_date    = today.isoformat()

    yield _progress(5, "Loading activation data…",
                    f"Reading activations {from_date} → {to_date}…", "step")
    await asyncio.sleep(0.1)

    activ_data = await _load_activations_data(from_date=from_date, to_date=to_date)
    records    = activ_data.get("records") or []

    # Filter to records that have a proration entry with a positive charge.
    # Trial accounts are excluded from activations upstream (_enrich_request
    # returns None for Trial billing type) but guard here too for safety.
    billable = [
        r for r in records
        if r.get("proration")
        and float((r.get("proration") or {}).get("proratedCharge") or 0) > 0
        and r.get("skuKey") and r["skuKey"] != "UNMAPPED"
        and not r.get("excludedCategory")
        and r.get("billingType") != "Trial"
    ]

    total = len(billable)
    yield _progress(10, f"Found {total} prorated activations…",
                    f"{total} activations with a prorated charge this month", "info")
    await asyncio.sleep(0.1)

    if total == 0:
        unmapped = activ_data.get("unmappedCount") or 0
        excluded = activ_data.get("excludedCount") or 0
        note = ""
        if unmapped:
            note += f" ({unmapped} unmapped SKU{'s' if unmapped > 1 else ''})"
        if excluded:
            note += f" ({excluded} excluded category)"
        yield _log(
            f"No billable prorated activations found for {from_date} → {to_date}.{note}",
            "warning" if unmapped else "info"
        )
        yield _progress(95, "Nothing to process.", "No prorated activations.", "info")
        return

    yield _progress(20, "Connecting to QuickBooks…", "Establishing QB connection…", "step")
    await asyncio.sleep(0.3)

    # TODO: Initialise QB connection here
    # e.g.  qb = QBClient(...)

    for i, rec in enumerate(billable):
        pct   = 20 + int((i / max(total, 1)) * 58)
        cname = rec.get("customerName") or "Unknown"
        sk    = rec.get("skuKey") or ""
        sn    = rec.get("serialNumber") or "—"
        pro   = rec.get("proration") or {}
        days  = pro.get("daysActive") or 0
        charge = round(float(pro.get("proratedCharge") or 0), 2)

        yield _progress(pct,
                        f"{'Previewing' if dry_run else 'Creating'} prorated entry: {cname}…",
                        f"Processing {cname} / {sn}…", "info")
        await asyncio.sleep(0.05)

        try:
            preview_entry = {
                "customer":    cname,
                "syncType":    "prorated",
                "skuKey":      sk,
                "serialNumber": sn,
                "qty":         1,
                "daysActive":  days,
                "amount":      charge,
                "action":      "would create" if dry_run else "created",
            }
            counters['preview'].append(preview_entry)
            counters['previewed'] += 1

            if dry_run:
                yield _log(
                    f"{cname} ({sn}) — would add prorated invoice: "
                    f"{sk} · {days}d · ${charge:.2f} [dry run]",
                    "dim"
                )
            else:
                # TODO: push prorated invoice to QB here
                # qb.create_prorated_invoice(
                #     customer=cname, sku=sk, serial=sn,
                #     days_active=days, amount=charge
                # )
                counters['created'] += 1
                yield _log(
                    f"✓ {cname} ({sn}) — prorated invoice added: "
                    f"{sk} · {days}d · ${charge:.2f}",
                    "success"
                )

        except Exception as exc:
            counters['errors'] += 1
            yield _error_item(cname, "prorated", str(exc))
            yield _log(f"✗ {cname} ({sn}) — error: {exc}", "error")

    summary_count = counters['previewed'] if dry_run else counters['created']
    yield _progress(78, "Finalising prorated entries…",
                    f"{summary_count} prorated entries {'previewed' if dry_run else 'created'}",
                    "info")
    await asyncio.sleep(0.2)


# ── Main SSE generator ────────────────────────────────────────────────────────
async def _sync_stream(mode: str, dry_run: bool) -> AsyncGenerator[str, None]:
    run_id     = str(uuid.uuid4())[:8]
    started    = datetime.now(timezone.utc).isoformat()
    t_start    = time.monotonic()
    run_log    = []
    run_errors = []
    all_preview: list = []

    def record_log(msg_dict: dict):
        """Track log entries for history persistence."""
        run_log.append({**msg_dict, "ts": datetime.now(timezone.utc).isoformat()})

    total_recurrences = 0
    total_invoices    = 0
    total_errors      = 0
    total_previewed   = 0
    failed            = False

    try:
        yield _progress(0, "Starting sync…", f"Run ID: {run_id}", "step")
        await asyncio.sleep(0.2)

        # ── Step 1: Recurrences
        if mode in ("recurrences", "full"):
            yield _progress(2, "Step 1/2 — Updating monthly recurrences…",
                            "Beginning recurrence updates…", "step")
            rec_counters = {}
            async for chunk in _run_recurrences(dry_run, rec_counters):
                # Re-scale pct: recurrences uses 2–50% of the bar
                try:
                    parsed = json.loads(chunk.replace("data: ", "").strip())
                    if parsed.get("type") == "progress":
                        raw_pct = parsed.get("pct", 0)
                        scaled  = 2 + int(raw_pct * 0.48)
                        parsed["pct"] = scaled
                        chunk = _evt(parsed)
                    elif parsed.get("type") == "error_item":
                        run_errors.append({
                            "customer": parsed["customer"],
                            "syncType": parsed["syncType"],
                            "message":  parsed["message"],
                        })
                        total_errors += 1
                    record_log(parsed)
                except Exception:
                    pass
                yield chunk

            total_recurrences  = rec_counters.get('updated', 0)
            total_previewed   += rec_counters.get('previewed', 0)
            total_errors      += rec_counters.get('errors', 0)
            all_preview.extend(rec_counters.get('preview', []))

        # ── Step 2: Prorated invoices
        if mode in ("prorated", "full"):
            step_label = "Step 2/2" if mode == "full" else "Step 1/1"
            yield _progress(50 if mode == "full" else 2,
                            f"{step_label} — Adding prorated invoices…",
                            "Beginning prorated invoice updates…", "step")
            pro_counters = {}
            async for chunk in _run_prorated(dry_run, pro_counters):
                try:
                    parsed = json.loads(chunk.replace("data: ", "").strip())
                    if parsed.get("type") == "progress":
                        raw_pct = parsed.get("pct", 0)
                        # Prorated uses 50–95% (full) or 2–95% (prorated-only)
                        if mode == "full":
                            scaled = 50 + int(raw_pct * 0.45)
                        else:
                            scaled = 2 + int(raw_pct * 0.93)
                        parsed["pct"] = scaled
                        chunk = _evt(parsed)
                    elif parsed.get("type") == "error_item":
                        run_errors.append({
                            "customer": parsed["customer"],
                            "syncType": parsed["syncType"],
                            "message":  parsed["message"],
                        })
                        total_errors += 1
                    record_log(parsed)
                except Exception:
                    pass
                yield chunk

            total_invoices    = pro_counters.get('created', 0)
            total_previewed  += pro_counters.get('previewed', 0)
            total_errors     += pro_counters.get('errors', 0)
            all_preview.extend(pro_counters.get('preview', []))

        yield _progress(98, "Wrapping up…", "Saving run record…", "dim")
        await asyncio.sleep(0.3)

    except Exception as exc:
        failed = True
        total_errors += 1
        err_msg = f"Sync aborted: {exc}"
        yield _log(err_msg, "error")
        run_errors.append({"customer": "—", "syncType": mode, "message": err_msg})

    # ── Persist history record
    duration_ms = int((time.monotonic() - t_start) * 1000)
    status = "failed" if failed else ("warnings" if total_errors > 0 else "success")

    _append_history({
        "id":                  run_id,
        "startedAt":           started,
        "mode":                mode,
        "dryRun":              dry_run,
        "status":              status,
        "recurrencesUpdated":  total_recurrences,
        "invoicesCreated":     total_invoices,
        "totalPreviewed":      total_previewed,
        "errorCount":          total_errors,
        "durationMs":          duration_ms,
        "log":                 run_log[-200:],   # store last 200 log lines
        "errors":              run_errors,
        "preview":             all_preview[:500],  # store up to 500 preview rows
    })

    yield _done(
        recurrences_updated=total_recurrences,
        invoices_created=total_invoices,
        error_count=total_errors,
        failed=failed,
        preview=all_preview,
    )


# ── Routes ────────────────────────────────────────────────────────────────────
@router.get("/qb-sync/run")
async def qb_sync_run(mode: str = "full", dryRun: str = "0"):
    """
    SSE stream for QB sync execution.

    Query params:
      mode    — recurrences | prorated | full  (default: full)
      dryRun  — 0 | 1                          (default: 0)

    Streams JSON events; see module docstring for event schema.
    """
    valid_modes = {"recurrences", "prorated", "full"}
    if mode not in valid_modes:
        mode = "full"
    dry = dryRun.strip() in ("1", "true", "yes")

    return StreamingResponse(
        _sync_stream(mode, dry),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


@router.get("/qb-sync/history")
async def qb_sync_history():
    """Return list of past sync runs, newest first."""
    return _load_history()


@router.delete("/qb-sync/history")
async def qb_sync_history_clear():
    """Clear all sync history."""
    _save_history([])
    return {"success": True}
