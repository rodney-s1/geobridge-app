from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
import os
import json

router = APIRouter()

# Geotab MyAdmin API URL from .env
MYADMIN_API_URL = os.getenv("MYADMIN_API_URL", "https://myadminapi.geotab.com/v2/MyAdminApi.ashx")

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
    print(f"[auth] Account selected: {request.account_id!r}")
    return {"success": True, "account_id": request.account_id}
