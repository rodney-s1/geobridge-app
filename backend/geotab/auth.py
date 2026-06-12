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
    "username": None
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

        # Store session for future API calls
        session_store["user_id"] = session_data.get("userId") or session_data.get("apiKey") or session_data.get("id") or session_data.get("userName")
        session_store["session_id"] = session_data.get("sessionId") or session_data.get("session") or session_data.get("id")
        session_store["username"] = session_data.get("name") or session_data.get("userName") or session_data.get("username")

        print("DEBUG session_store after login:", session_store)

        return {
            "success": True,
            "user_id": session_data["userId"],
            "session_id": session_data["sessionId"],
            "name": session_data["name"],
            "roles": session_data.get("roles", [])
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
