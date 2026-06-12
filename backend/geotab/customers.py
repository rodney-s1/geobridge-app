from fastapi import APIRouter, HTTPException
from .auth import myadmin_call, session_store

router = APIRouter()

# ─── Get All Customers ────────────────────────────────────────
@router.get("/customers")
async def get_customers():
    if not session_store["session_id"]:
        raise HTTPException(status_code=401, detail="Not logged in")

    all_customers = []
    page = 0

    try:
        # MyAdmin returns 50 customers at a time
        # We keep fetching until we get them all
        while True:
            result = await myadmin_call("GetCustomersAsync", {
                "apiKey": session_store["user_id"],
                "sessionId": session_store["session_id"],
                "page": page
            })

            if "result" not in result:
                break

            customers = result["result"]

            if not customers or len(customers) == 0:
                break

            all_customers.extend(customers)

            # If we got less than 50, we've reached the last page
            if len(customers) < 50:
                break

            page += 1

        return {
            "success": True,
            "total": len(all_customers),
            "customers": all_customers
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Get Single Customer ──────────────────────────────────────
@router.get("/customers/{account_id}")
async def get_customer(account_id: str):
    if not session_store["session_id"]:
        raise HTTPException(status_code=401, detail="Not logged in")

    try:
        result = await myadmin_call("GetCustomersAsync", {
            "apiKey": session_store["user_id"],
            "sessionId": session_store["session_id"],
            "forAccount": account_id
        })

        if "result" not in result:
            raise HTTPException(status_code=404, detail="Customer not found")

        return {
            "success": True,
            "customer": result["result"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Get Device Contracts for a Customer ─────────────────────
@router.get("/customers/{account_id}/devices")
async def get_customer_devices(account_id: str):
    if not session_store["session_id"]:
        raise HTTPException(status_code=401, detail="Not logged in")

    all_devices = []
    next_id = 0

    try:
        # MyAdmin returns 1000 devices at a time
        # We keep fetching until we get them all
        while True:
            result = await myadmin_call("GetDeviceContractsByPage", {
                "apiKey": session_store["user_id"],
                "sessionId": session_store["session_id"],
                "forAccount": account_id,
                "nextId": next_id
            })

            if "result" not in result:
                break

            devices = result["result"]

            if not devices or len(devices) == 0:
                break

            all_devices.extend(devices)

            # If we got less than 1000, we've reached the last page
            if len(devices) < 1000:
                break

            # Use the last device ID to get the next page
            next_id = devices[-1]["id"]

        return {
            "success": True,
            "total": len(all_devices),
            "devices": all_devices
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
