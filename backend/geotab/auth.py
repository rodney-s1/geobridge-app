from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
import os
import json

router = APIRouter()

# Geotab MyAdmin API URL from .env
MYADMIN_API_URL = os.getenv("MYADMIN_API_URL", "https://myadminapi.geotab.com/v2/MyAdminApi.ashx")

# Store session in memory while app is running
session_store = {
    "user_id": None,
    "session_id": None,
    "username": None,
    "accounts": [],
    "account_id": None,   # currently selected accountId for API calls
}

# ─── Request Models ───────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

# ─── Helper: Make MyAdmin API Call ───────────────────────────
async def myadmin_call(method: str, params: dict):
    payload = {
        "method": method,
        "params": params
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            MYADMIN_API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()

# ─── Login Route ─────────────────────────────────────────────
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

        # DEBUG - print full login response to see exact field names
        print("DEBUG Authenticate response keys:", list(session_data.keys()) if isinstance(session_data, dict) else session_data)
        print("DEBUG Full session_data:", json.dumps(session_data, indent=2)[:1000])

        # Extract accounts list
        accounts = session_data.get("accounts") or []

        # Store session for future API calls
        session_store["user_id"] = session_data.get("userId")
        session_store["session_id"] = session_data.get("sessionId")
        session_store["username"] = session_data.get("name") or session_data.get("userName")
        session_store["accounts"] = accounts

        print("DEBUG session_store after login:", {k: v for k, v in session_store.items() if k != "accounts"})

        return {
            "success": True,
            "user_id": session_data["userId"],
            "session_id": session_data["sessionId"],
            "name": session_data["name"],
            "roles": session_data.get("roles", []),
            "accounts": accounts,
        }

    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"API connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Logout Route ─────────────────────────────────────────────
@router.post("/logout")
async def logout():
    session_store["user_id"] = None
    session_store["session_id"] = None
    session_store["username"] = None
    session_store["accounts"] = []
    session_store["account_id"] = None
    return {"success": True, "message": "Logged out successfully"}

# ─── Session Check Route ──────────────────────────────────────
@router.get("/session")
async def get_session():
    if not session_store["session_id"]:
        raise HTTPException(status_code=401, detail="No active session")
    return {
        "active": True,
        "user_id": session_store["user_id"],
        "username": session_store["username"]
    }

# ─── Accounts Route ───────────────────────────────────────────
@router.get("/accounts")
async def get_accounts():
    if not session_store["session_id"]:
        raise HTTPException(status_code=401, detail="Not logged in")
    return {
        "accounts": session_store.get("accounts") or []
    }

# ─── Select Account Route ─────────────────────────────────────
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
    print(f"DEBUG selected account_id: {request.account_id}")
    return {"success": True, "account_id": request.account_id}
