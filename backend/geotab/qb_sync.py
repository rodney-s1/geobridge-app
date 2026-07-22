"""
qb_sync.py — QuickBooks sync router

SSE endpoint: GET /api/qb-sync/run?mode={recurrences|prorated|full}&dryRun={0|1}
History:      GET /api/qb-sync/history
Clear:        DELETE /api/qb-sync/history

The /run endpoint streams Server-Sent Events as JSON objects:

  {"type": "progress", "pct": 0-100, "label": "...", "log": "...", "logType": "info|step|success|warning|error"}
  {"type": "log",      "message": "...", "logType": "..."}
  {"type": "error_item", "customer": "...", "syncType": "recurrence|prorated", "message": "..."}
  {"type": "done",     "recurrencesUpdated": N, "invoicesCreated": N, "errorCount": N, "failed": bool}

DEV TEAM INTEGRATION NOTES
───────────────────────────
This file contains a fully-wired stub that:
  • Demonstrates the complete SSE protocol the frontend expects
  • Persists run history to qb_sync_history.json
  • Is structured so the actual QB API calls slot in at the marked
    TODO blocks without changing the event protocol

To implement real QB pushes:
  1. In _run_recurrences(): replace the TODO block with calls to the
     QB SDK / IIF writer / REST API that upsert memorized transactions
     in the "Monthly Recurrences" group.
  2. In _run_prorated(): replace the TODO block with calls that write
     entries into the "Prorated Service Invoices" group.
  3. Both helpers receive the reconciliation data dict so they have
     full access to device counts, prices, and customer names.

The stub emits simulated progress events so the UI is fully testable
before any real QB integration is built.
"""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── Persistence ───────────────────────────────────────────────────────────────
_HERE        = os.path.dirname(__file__)
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
    history.insert(0, run)          # newest first
    history = history[:100]         # keep last 100 runs
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
          error_count: int, failed: bool = False) -> str:
    return _evt({"type": "done",
                 "recurrencesUpdated": recurrences_updated,
                 "invoicesCreated":    invoices_created,
                 "errorCount":         error_count,
                 "failed":             failed})


# ── QB sync logic (stubs — replace TODO blocks with real QB calls) ─────────────
async def _run_recurrences(dry_run: bool, counters: dict) -> AsyncGenerator:
    """
    Push monthly recurrence updates to QB 'Monthly Recurrences' group.

    TODO: Replace the stub body with real QB API/IIF calls.
          Yield SSE strings throughout to keep the UI progress bar moving.
          Use _error_item() to report per-customer failures without aborting.
          Increment counters['updated'] and counters['errors'] in place.
    """
    counters['updated'] = 0
    counters['errors']  = 0
    yield _progress(5, "Loading reconciliation data…", "Reading customer data…", "step")
    await asyncio.sleep(0.4)

    # TODO: Load reconciliation data
    # from geotab.reconciliation import build_reconciliation_data
    # recon = await build_reconciliation_data()
    # customers = recon["customers"]

    # STUB: simulate customer list
    stub_customers = [
        "Angel Electric", "City of Raleigh", "Hoopaugh Grading Co",
        "Suburban Supply", "Total Cleaning", "Interstate Nationalease",
    ]

    yield _progress(15, "Connecting to QuickBooks…", "Establishing QB connection…", "step")
    await asyncio.sleep(0.5)

    # TODO: Initialise QB connection / IIF writer here

    updated = 0
    errors  = 0
    total   = len(stub_customers)

    for i, cust in enumerate(stub_customers):
        pct = 15 + int((i / total) * 60)
        yield _progress(pct, f"Updating recurrence: {cust}…",
                        f"Processing {cust}…", "info")
        await asyncio.sleep(0.3)

        # TODO: push memorized transaction for `cust` to QB here
        # try:
        #     qb_client.upsert_memorized_transaction(cust, amount, sku_lines)
        #     updated += 1
        #     yield _log(f"✓ {cust} — recurrence updated", "success")
        # except QBException as e:
        #     errors += 1
        #     yield _error_item(cust, "recurrence", str(e))

        # STUB behaviour
        if not dry_run:
            counters['updated'] += 1
            yield _log(f"{cust} — recurrence updated (stub)", "success")
        else:
            yield _log(f"{cust} — would update recurrence [dry run]", "dim")

    yield _progress(78, "Finalising recurrences…", f"{counters['updated']} recurrences processed", "info")
    await asyncio.sleep(0.3)


async def _run_prorated(dry_run: bool, counters: dict) -> AsyncGenerator:
    """
    Push prorated invoice entries to QB 'Prorated Service Invoices' group.

    TODO: Replace the stub body with real QB API/IIF calls.
          Yield SSE strings throughout to keep the UI progress bar moving.
          Use _error_item() to report per-customer failures without aborting.
          Increment counters['created'] and counters['errors'] in place.
    """
    counters['created'] = 0
    counters['errors']  = 0
    yield _progress(5, "Loading activation data…", "Reading activation records…", "step")
    await asyncio.sleep(0.4)

    # TODO: Load activations / prorated invoice data
    # from geotab.activations import get_recent_activations
    # activations = await get_recent_activations()

    # STUB: simulate activation list
    stub_activations = [
        "Atrium Health - Charlotte",
        "Suburban Supply (Cameras)",
        "Go To Team",
    ]

    yield _progress(20, "Connecting to QuickBooks…", "Establishing QB connection…", "step")
    await asyncio.sleep(0.4)

    created = 0
    errors  = 0
    total   = len(stub_activations)

    for i, cust in enumerate(stub_activations):
        pct = 20 + int((i / total) * 55)
        yield _progress(pct, f"Creating prorated entry: {cust}…",
                        f"Processing {cust}…", "info")
        await asyncio.sleep(0.4)

        # TODO: create memorized prorated invoice for `cust` in QB here
        # try:
        #     qb_client.create_prorated_invoice(cust, prorated_amount, days_active)
        #     created += 1
        #     yield _log(f"✓ {cust} — prorated invoice added", "success")
        # except QBException as e:
        #     errors += 1
        #     yield _error_item(cust, "prorated", str(e))

        # STUB behaviour
        if not dry_run:
            counters['created'] += 1
            yield _log(f"{cust} — prorated invoice added (stub)", "success")
        else:
            yield _log(f"{cust} — would add prorated invoice [dry run]", "dim")

    yield _progress(78, "Finalising prorated entries…", f"{counters['created']} prorated invoices processed", "info")
    await asyncio.sleep(0.3)


# ── Main SSE generator ────────────────────────────────────────────────────────
async def _sync_stream(mode: str, dry_run: bool) -> AsyncGenerator[str, None]:
    run_id    = str(uuid.uuid4())[:8]
    started   = datetime.now(timezone.utc).isoformat()
    t_start   = time.monotonic()
    run_log   = []
    run_errors = []

    def record_log(msg_dict: dict):
        """Track log entries for history persistence."""
        run_log.append({**msg_dict, "ts": datetime.now(timezone.utc).isoformat()})

    total_recurrences = 0
    total_invoices    = 0
    total_errors      = 0
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

            total_recurrences = rec_counters.get('updated', 0)
            total_errors     += rec_counters.get('errors', 0)

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

            total_invoices = pro_counters.get('created', 0)
            total_errors  += pro_counters.get('errors', 0)

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
        "id":                 run_id,
        "startedAt":          started,
        "mode":               mode,
        "dryRun":             dry_run,
        "status":             status,
        "recurrencesUpdated": total_recurrences,
        "invoicesCreated":    total_invoices,
        "errorCount":         total_errors,
        "durationMs":         duration_ms,
        "log":                run_log[-200:],   # store last 200 log lines
        "errors":             run_errors,
    })

    yield _done(
        recurrences_updated=total_recurrences,
        invoices_created=total_invoices,
        error_count=total_errors,
        failed=failed,
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
            "Cache-Control":       "no-cache",
            "X-Accel-Buffering":   "no",
            "Connection":          "keep-alive",
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
