from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import httpx
import os
import json
from ._data_dir import _DATA_DIR

router = APIRouter()

# Geotab MyAdmin API URL from .env
MYADMIN_API_URL = os.getenv("MYADMIN_API_URL", "https://myadminapi.geotab.com/v2/MyAdminApi.ashx")

# V3 endpoint — currently only used for GetDeviceContracts (page-numbered
# pagination with an up-front `total` count, replacing v2's cursor-chained
# GetDeviceContractsByPage). See customers.py's _fetch_myadmin_customers()
# for the migration; kept as a separate URL/helper so other v2 calls are
# completely unaffected.
MYADMIN_API_V3_URL = os.getenv("MYADMIN_API_V3_URL", "https://myadminapi.geotab.com/v3/MyAdminApi.ashx")

# ---------------------------------------------------------------------------
# Persistent HTTP client (Option 5)
# ---------------------------------------------------------------------------
# Re-using a single AsyncClient across all myadmin_call() invocations means
# TCP connections and TLS sessions are kept alive between pages, saving
# ~200-500 ms of handshake overhead per request (multiplied across 100+ pages
# during a full contract sync).
#
# Limits:
#   max_keepalive_connections=10  — pool size; more than enough for our
#                                   sliding-window concurrency (WINDOW_SIZE=4)
#   max_connections=20            — hard ceiling on simultaneous sockets
#   keepalive_expiry=30           — idle connections closed after 30 s so we
#                                   don't hold sockets open between syncs
# ---------------------------------------------------------------------------
_http_client = httpx.AsyncClient(
    limits=httpx.Limits(
        max_keepalive_connections=10,
        max_connections=20,
        keepalive_expiry=30,
    ),
    # Default timeout applied unless overridden per-call.
    timeout=120.0,
)

# Store session in memory while app is running
session_store = {
    "user_id": None,
    "session_id": None,
    "username": None,
    "accounts": [],
    "account_id": None,   # currently selected accountId for API calls
}

# ---------------------------------------------------------------------------
# Session persistence — "stay logged in" (Option #1)
# ---------------------------------------------------------------------------
# A MyAdmin sessionId is valid for up to 1 week (per Geotab's Authenticate
# docs) or until the MyGeotab server restarts, whichever comes first.  The
# Python backend is a fresh process every time Electron launches though, so
# session_store above is always empty on startup — the user had to log in
# on every single launch even though the underlying MyAdmin session was
# often still perfectly valid.
#
# Fix: persist ONLY the session token (never the password) to a local JSON
# file in the per-user data directory.  On startup, load it and validate it
# with one cheap authenticated MyAdmin call before trusting it — if MyAdmin
# says the session has expired, we silently discard the file and fall back
# to the normal Login screen.
#
# This file is intentionally NOT added to ADMIN_ONLY_FILES / ALL_USER_FILES
# in s3_sync.py — it is per-machine, per-user sensitive session state and
# must never be synced to S3 or shared between machines.
# ---------------------------------------------------------------------------
SESSION_FILE = os.path.join(_DATA_DIR, "session.json")


def _save_session_to_disk() -> None:
    """Persist the current session token to disk so it survives app restarts.

    Only the session token + display info is written — never the password.
    Best-effort: failures are logged but never raised (a stale/missing
    session file just means the user logs in again next launch).
    """
    try:
        tmp = SESSION_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "user_id":    session_store.get("user_id"),
                "session_id": session_store.get("session_id"),
                "username":   session_store.get("username"),
                "accounts":   session_store.get("accounts") or [],
                "account_id": session_store.get("account_id"),
            }, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SESSION_FILE)
    except Exception as e:
        print(f"[auth] WARNING: could not persist session to disk: {e}")


def _clear_session_from_disk() -> None:
    """Remove the persisted session file (called on logout)."""
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception as e:
        print(f"[auth] WARNING: could not remove session file: {e}")


def _load_session_from_disk() -> Optional[dict]:
    """Load a previously-persisted session token from disk, if present."""
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user_id") and data.get("session_id"):
                return data
    except Exception as e:
        print(f"[auth] WARNING: could not read session file: {e}")
    return None


async def _validate_session(user_id: str, session_id: str) -> bool:
    """Return True if this (userId, sessionId) pair is still accepted by MyAdmin.

    Uses GetDevicePlans as a cheap, side-effect-free authenticated call —
    it requires a valid session but returns a small, account-agnostic payload
    (no forAccount / pagination needed), so it's a lightweight way to check
    "is this session still valid?" without pulling any real business data.

    A SessionExpiredException (or any other error) from MyAdmin — or a
    network failure — is treated as "not valid", so the app safely falls
    back to the normal Login screen rather than getting stuck.
    """
    try:
        result = await myadmin_call("GetDevicePlans", {
            "apiKey":    user_id,
            "sessionId": session_id,
        }, timeout=15.0)
        return "error" not in result
    except Exception as e:
        print(f"[auth] Session validation failed: {e}")
        return False

# ---------------------------------------------------------------------------
# Auth dependency — import and use in any router that needs protection:
#   from .auth import require_session
#   @router.get("/my-route")
#   async def my_route(_: None = Depends(require_session)):
# ---------------------------------------------------------------------------
def require_session():
    """FastAPI dependency: raises 401 if no active MyAdmin session."""
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in to MyAdmin")

# --- Request Models -------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str

# --- Helper: Make MyAdmin API Call ---------------------------
async def myadmin_call(method: str, params: dict, timeout: float = 120.0):
    """
    Make a JSON-RPC call to the Geotab MyAdmin API.

    Uses the module-level persistent AsyncClient (_http_client) so that TCP
    connections and TLS sessions are reused across consecutive paginated calls
    (e.g. the 100+ GetDeviceContractsByPage pages in a full sync).

    Default timeout is 120 s — long enough for paginated calls across large
    accounts.  Pass a custom timeout for one-off calls that need a different
    limit.
    """
    payload = {
        "method": method,
        "params": params
    }
    response = await _http_client.post(
        MYADMIN_API_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


async def myadmin_call_v3(method: str, params: dict, pagination: dict, timeout: float = 60.0):
    """
    Make a JSON-RPC call to the Geotab MyAdmin V3 API, which uses a top-level
    `pagination` object (`{"page": N, "perPage": M}`) instead of v2's
    cursor-based `nextId` field.

    V3 responses include a `pagination` block echoing back page/perPage plus
    a `total` record count — see GetDeviceContracts usage in customers.py.

    Raises httpx.HTTPStatusError on non-2xx (including 429 rate-limit, which
    the caller should catch and back off / fall back to v2 on).
    """
    payload = {
        "id": -1,
        "method": method,
        "params": params,
        "pagination": pagination,
    }
    response = await _http_client.post(
        MYADMIN_API_V3_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()

# --- Login Route ---------------------------------------------
@router.post("/login")
async def login(request: LoginRequest):
    try:
        result = await myadmin_call("Authenticate", {
            "username": request.username,
            "password": request.password
        })

        if "result" not in result:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        session_data = result["result"]

        # Extract accounts list
        accounts = session_data.get("accounts") or []

        # Store session for future API calls
        session_store["user_id"] = session_data.get("userId")
        session_store["session_id"] = session_data.get("sessionId")
        session_store["username"] = session_data.get("name") or session_data.get("userName")
        session_store["accounts"] = accounts

        # Persist the session token to disk so the app can resume without a
        # fresh login next launch (see _save_session_to_disk() docstring).
        _save_session_to_disk()

        print(f"[auth] Login successful: user={session_store['username']!r} accounts={len(accounts)}")

        return {
            "success": True,
            "name": session_data["name"],
            "roles": session_data.get("roles", []),
            "accounts": accounts,
        }

    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"API connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Logout Route ---------------------------------------------
@router.post("/logout")
async def logout():
    session_store["user_id"] = None
    session_store["session_id"] = None
    session_store["username"] = None
    session_store["accounts"] = []
    session_store["account_id"] = None
    _clear_session_from_disk()
    return {"success": True, "message": "Logged out successfully"}

# --- Session Check Route --------------------------------------
@router.get("/session")
async def get_session():
    if not session_store["session_id"]:
        raise HTTPException(status_code=401, detail="No active session")
    return {
        "active": True,
        "username": session_store["username"]
    }

# --- Session Restore Route -------------------------------------
# Called once by the frontend on startup, BEFORE showing the Login screen.
# Attempts to silently resume a previously-persisted MyAdmin session so the
# user isn't forced to log in on every launch (see module docstring above).
@router.post("/session/restore")
async def restore_session():
    # Already have a live session in memory this run — nothing to do.
    if session_store.get("session_id"):
        return {
            "restored": True,
            "name": session_store.get("username"),
            "accounts": session_store.get("accounts") or [],
            "account_id": session_store.get("account_id"),
        }

    saved = _load_session_from_disk()
    if not saved:
        return {"restored": False, "reason": "no_saved_session"}

    is_valid = await _validate_session(saved["user_id"], saved["session_id"])
    if not is_valid:
        # Session expired (>1 week old, or MyGeotab server restarted) —
        # discard the stale file so we don't keep retrying it every launch.
        _clear_session_from_disk()
        return {"restored": False, "reason": "session_expired"}

    # Session still valid — restore it into memory and let the user straight
    # into the app without re-entering credentials.
    session_store["user_id"]    = saved["user_id"]
    session_store["session_id"] = saved["session_id"]
    session_store["username"]   = saved["username"]
    session_store["accounts"]   = saved.get("accounts") or []
    session_store["account_id"] = saved.get("account_id")

    print(f"[auth] Session restored from disk: user={session_store['username']!r}")

    return {
        "restored": True,
        "name": session_store.get("username"),
        "accounts": session_store.get("accounts") or [],
        "account_id": session_store.get("account_id"),
    }

# --- Accounts Route -------------------------------------------
@router.get("/accounts")
async def get_accounts():
    if not session_store["session_id"]:
        raise HTTPException(status_code=401, detail="Not logged in")
    return {
        "accounts": session_store.get("accounts") or []
    }

# --- Select Account Route -------------------------------------
class SelectAccountRequest(BaseModel):
    account_id: str

@router.post("/select-account")
async def select_account(request: SelectAccountRequest):
    if not session_store["session_id"]:
        raise HTTPException(status_code=401, detail="Not logged in")
    # Validate it's one of the user's accounts
    valid_ids = [a["accountId"] for a in (session_store.get("accounts") or [])]
    if request.account_id not in valid_ids:
        raise HTTPException(status_code=400, detail=f"Invalid account: {request.account_id}")
    session_store["account_id"] = request.account_id
    # Keep the persisted session file in sync so a restore picks the same account.
    _save_session_to_disk()
    print(f"[auth] Account selected: {request.account_id!r}")
    return {"success": True, "account_id": request.account_id}
