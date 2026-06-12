from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional
import csv
import io
from .auth import myadmin_call, session_store

router = APIRouter()

# ─── In-memory stores (replace with SQLite later) ─────────────────────────────
# billing_overrides: { customer_id -> billing_type }
billing_overrides: dict[str, str] = {}

# qb_customers: { normalized_name -> { accountNo, billingType, terms, balance, ... } }
qb_customers: dict[str, dict] = {}

# qb_items: list of item/price records
qb_items: list[dict] = []

# ─── Billing type map from QB Job Type field ───────────────────────────────────
QB_JOB_TYPE_MAP = {
    "standard":               "Standard",
    "cua":                    "CUA",
    "sourcewell":             "Sourcewell",
    "hanover":                "Hanover",
    "han-cs":                 "Han-CS",
    "hancs":                  "Han-CS",
    "charge upon activation": "Charge Upon Activation",
    "cua - charge upon activation": "Charge Upon Activation",
    "check before sending":   "Check Before Sending",
    "reseller":               "Reseller",
    "in collections":         "In Collections",
    "collections":            "In Collections",
    "terminated":             "Terminated",
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
    """Merge MyAdmin database record with QB data and billing overrides."""
    # GetOwnDatabases returns: databaseName, accountId, customerName, deviceCount, etc.
    cid = str(
        customer.get("accountId") or
        customer.get("id") or
        customer.get("databaseName") or ""
    )
    cname = (
        customer.get("customerName") or
        customer.get("name") or
        customer.get("companyName") or
        customer.get("databaseName") or ""
    )

    # Look up QB data by normalized name
    qb = qb_customers.get(normalize(cname)) or {}

    # Determine billing type: override > QB job type > Unknown
    billing_type = (
        billing_overrides.get(cid)
        or qb.get("billingType")
        or map_billing_type(customer.get("jobType") or customer.get("customerType") or "")
    )

    return {
        "id":              cid,
        "name":            cname,
        "accountNo":       qb.get("accountNo") or customer.get("accountNo") or "",
        "billingType":     billing_type,
        "primaryDatabase": customer.get("databaseName") or customer.get("primaryDatabase") or "",
        "deviceCount":     customer.get("deviceCount") or customer.get("numberOfDevices") or 0,
        "terms":           qb.get("terms") or "",
        "balance":         float(qb.get("balance") or 0),
        "hasQbData":       bool(qb),
        "email":           customer.get("email") or "",
        "phone":           customer.get("phone") or "",
        "address":         customer.get("address") or "",
    }


# ─── GET /api/customers  (paginated, search, billing filter) ──────────────────
@router.get("/customers")
async def get_customers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query(""),
    billing_type: str = Query(""),
):
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in")

    try:
        # Fetch one page from MyAdmin
        # DEBUG - show exactly what's in session_store
        print("DEBUG session_store at sync:", {
            "user_id": session_store.get("user_id"),
            "session_id": session_store.get("session_id"),
            "account_id": session_store.get("account_id"),
        })

        # Use GetOwnDatabases — returns list of customer databases for the account
        # (GetCustomersAsync requires CONTACT-VIEW role which this account doesn't have)
        result = await myadmin_call("GetOwnDatabases", {
            "apiKey":      session_store["user_id"],
            "sessionId":   session_store["session_id"],
            "forAccount":  session_store.get("account_id"),
        })

        # DEBUG - print full MyAdmin response to backend console
        import json
        print("DEBUG MyAdmin response:", json.dumps(result, indent=2)[:2000])

        raw = result.get("result") or []
        customers = [enrich_customer(c) for c in raw]

        # Apply search filter
        if search:
            s = search.lower()
            customers = [
                c for c in customers
                if s in c["name"].lower()
                or s in (c["accountNo"] or "").lower()
                or s in (c["primaryDatabase"] or "").lower()
            ]

        # Apply billing type filter
        if billing_type:
            customers = [c for c in customers if c["billingType"] == billing_type]

        return {
            "customers": customers,
            "page":      page,
            "pageSize":  page_size,
            "hasMore":   False,   # GetOwnDatabases returns all at once
            "total":     len(customers),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── GET /api/customers/qb-data/summary ───────────────────────────────────────
# IMPORTANT: must be defined BEFORE the /{account_id} wildcard route
@router.get("/customers/qb-data/summary")
async def get_qb_summary():
    if not qb_customers:
        return {
            "customersLoaded": 0,
            "itemsLoaded":     len(qb_items),
            "billingTypeBreakdown": {},
        }

    breakdown: dict[str, int] = {}
    for qb in qb_customers.values():
        bt = qb.get("billingType") or "Unknown"
        breakdown[bt] = breakdown.get(bt, 0) + 1

    return {
        "customersLoaded":     len(qb_customers),
        "itemsLoaded":         len(qb_items),
        "billingTypeBreakdown": breakdown,
    }


# ─── POST /api/customers/import-qb  (CSV upload) ──────────────────────────────
# IMPORTANT: must be defined BEFORE the /{account_id} wildcard route
@router.post("/customers/import-qb")
async def import_qb_customers(file: UploadFile = File(...)):
    global qb_customers
    content = await file.read()

    try:
        text = content.decode("utf-8-sig")  # handles BOM from QB exports
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty")

    # Detect QB Customer List format vs other formats
    headers = [h.strip() for h in (reader.fieldnames or [])]

    imported = 0
    skipped  = 0

    for row in rows:
        # Strip whitespace from all values
        row = {k.strip(): (v or "").strip() for k, v in row.items()}

        # QB Customer List export columns (may vary slightly by QB version)
        name = (
            row.get("Customer")
            or row.get("Name")
            or row.get("Customer Name")
            or row.get("Full Name")
            or ""
        )
        if not name:
            skipped += 1
            continue

        job_type = (
            row.get("Job Type")
            or row.get("Customer Type")
            or row.get("Type")
            or ""
        )
        account_no = (
            row.get("Account No.")
            or row.get("Account Number")
            or row.get("Account #")
            or row.get("Acct No")
            or ""
        )
        terms = row.get("Terms") or row.get("Payment Terms") or ""
        balance_str = (
            row.get("Balance Total")
            or row.get("Balance")
            or row.get("Current Balance")
            or "0"
        )
        # Clean up balance — remove $, commas, parens for negatives
        balance_str = balance_str.replace("$", "").replace(",", "").strip()
        if balance_str.startswith("(") and balance_str.endswith(")"):
            balance_str = "-" + balance_str[1:-1]
        try:
            balance = float(balance_str) if balance_str else 0.0
        except ValueError:
            balance = 0.0

        billing_type = map_billing_type(job_type)

        qb_customers[normalize(name)] = {
            "name":        name,
            "accountNo":   account_no,
            "billingType": billing_type,
            "jobType":     job_type,
            "terms":       terms,
            "balance":     balance,
        }
        imported += 1

    return {
        "success": True,
        "message": f"{imported} customers imported, {skipped} skipped",
        "imported": imported,
        "skipped":  skipped,
        "total":    len(qb_customers),
    }


# ─── GET /api/customers/{id}  (single customer + devices) ────────────────────
# NOTE: This wildcard route must remain BELOW all fixed-path routes like
# /customers/import-qb and /customers/qb-data/summary so FastAPI matches
# those specific paths first.
@router.get("/customers/{account_id}")
async def get_customer(account_id: str):
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in")

    try:
        # Fetch device contracts for this customer
        all_devices = []
        next_id = 0

        while True:
            result = await myadmin_call("GetDeviceContractsByPage", {
                "apiKey":     session_store["user_id"],
                "sessionId":  session_store["session_id"],
                "forAccount": account_id,
                "nextId":     next_id,
            })

            devices = result.get("result") or []
            if not devices:
                break

            all_devices.extend(devices)

            if len(devices) < 1000:
                break
            next_id = devices[-1].get("id", 0)

        # Normalize device fields for frontend
        normalized = []
        for d in all_devices:
            normalized.append({
                "serialNumber":      d.get("serialNumber") or d.get("SerialNumber") or "",
                "deviceType":        d.get("productName") or d.get("deviceType") or "",
                "activeBillingPlan": d.get("activeBillingPlan") or d.get("ratePlanName") or "",
                "ratePlanCode":      d.get("ratePlanCode") or d.get("planCode") or "",
                "database":          d.get("databaseName") or d.get("database") or "",
                "status":            "Active" if d.get("isActive", True) else "Inactive",
                "contractStartDate": d.get("contractStartDate") or "",
                "contractEndDate":   d.get("contractEndDate") or "",
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


# ─── POST /api/customers/{id}/billing-type ────────────────────────────────────
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
    return {"success": True, "customerId": account_id, "billingType": body.billing_type}


# ─── GET /api/customers/{id}/devices  (alias for detail endpoint) ─────────────
@router.get("/customers/{account_id}/devices")
async def get_customer_devices(account_id: str):
    return await get_customer(account_id)
