from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional
import csv
import io
import json
import os
from .auth import myadmin_call, session_store

router = APIRouter()

# ─── CELU01 is the only MyAdmin account we pull device contracts for ──────────
MYADMIN_ACCOUNT = "CELU01"

# ─── Disk persistence paths ───────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
QB_DATA_FILE   = os.path.join(_HERE, "qb_customers.json")
OVERRIDES_FILE = os.path.join(_HERE, "billing_overrides.json")

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
billing_overrides: dict[str, str] = _load_json(OVERRIDES_FILE, {})
qb_customers: dict[str, dict]     = _load_json(QB_DATA_FILE, {})
qb_items: list[dict]              = []

_qb_loaded = bool(qb_customers)
print(f"[customers] QB data: {len(qb_customers)} customers loaded from disk"
      if _qb_loaded else "[customers] QB data: no saved file — import a CSV to populate")

# ─── Billing type map from QB Job Type field ───────────────────────────────────
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
    company_id   = str(customer.get("companyId")   or "")
    company_name = customer.get("customerName") or ""

    # Match QB data by normalized company name
    qb = qb_customers.get(normalize(company_name)) or {}

    # Prefer QB's canonical name if matched, otherwise use MyAdmin name
    display_name = qb.get("name") or company_name or f"Company {company_id}"

    # Stable ID for overrides: use companyId (numeric, stable) as key
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
        "primaryDatabase": company_id,          # shown in "Primary Database" col
        "deviceCount":     customer.get("activeDevices") or 0,
        "terms":           qb.get("terms") or "",
        "balance":         float(qb.get("balance") or 0),
        "hasQbData":       bool(qb),
        "email":           customer.get("email") or "",
        "phone":           customer.get("phone") or "",
        "address":         customer.get("address") or "",
    }


# ─── GET /api/customers ────────────────────────────────────────────────────────
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
        # ── Fetch ALL device contracts for CELU01 ────────────────────────────
        # Real API response structure (confirmed from debug output):
        #   contract["id"]                               → contract id (int)
        #   contract["account"]["accountId"]             → "CELU01"
        #   contract["device"]["serialNumber"]           → device serial
        #   contract["device"]["deviceType"]["name"]     → "GO" etc.
        #   contract["userContact"]["userCompany"]["id"] → company id (int, stable grouping key)
        #   contract["userContact"]["userCompany"]["name"] → customer/company name
        #   contract["isTerminated"]                     → bool
        #   contract["startDate"] / contract["endDate"]
        #   contract["productCode"]
        all_contracts = []
        next_id = 0
        page_num = 0
        while True:
            page_num += 1
            print(f"DEBUG: Fetching page {page_num} (nextId={next_id}, account={MYADMIN_ACCOUNT})…")
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
            print(f"DEBUG: Page {page_num} returned {len(batch)} contracts")
            if not batch:
                break
            all_contracts.extend(batch)
            if len(batch) < 1000:
                break
            next_id = batch[-1].get("id", 0)

        # ── Group by userCompany id → one entry per customer ─────────────────
        # Key = userCompany["id"] (numeric, stable).
        # Name = userCompany["name"] (shown to user, matched against QB).
        company_map: dict[str, dict] = {}

        for c in all_contracts:
            user_contact = c.get("userContact") or {}
            user_company = user_contact.get("userCompany") or {}
            company_id   = str(user_company.get("id") or "")
            company_name = user_company.get("name") or ""
            terminated   = bool(c.get("isTerminated"))

            # Skip contracts with no company info
            key = company_id or company_name
            if not key:
                continue

            if key not in company_map:
                company_map[key] = {
                    "companyId":     company_id,
                    "customerName":  company_name,
                    "activeDevices": 0,
                    "totalDevices":  0,
                }

            # Use best name we've seen (prefer non-empty)
            if not company_map[key]["customerName"] and company_name:
                company_map[key]["customerName"] = company_name

            company_map[key]["totalDevices"] += 1
            if not terminated:
                company_map[key]["activeDevices"] += 1

        # ── Filter out companies with ONLY terminated devices ─────────────────
        # Also skip the special "* Terminated Devices" catch-all company
        raw = [
            v for v in company_map.values()
            if v["activeDevices"] > 0
            and not v["customerName"].startswith("* Terminated")
        ]
        print(f"DEBUG: {len(all_contracts)} contracts → {len(company_map)} companies → {len(raw)} with active devices")

        customers = [enrich_customer(c) for c in raw]

        # ── Search filter ─────────────────────────────────────────────────────
        if search:
            s = search.lower()
            customers = [
                c for c in customers
                if s in c["name"].lower()
                or s in (c["accountNo"] or "").lower()
                or s in (c["primaryDatabase"] or "").lower()
            ]

        # ── Billing type filter ───────────────────────────────────────────────
        if billing_type:
            customers = [c for c in customers if c["billingType"] == billing_type]

        # ── Sort by name ──────────────────────────────────────────────────────
        customers.sort(key=lambda c: c["name"].lower())

        return {
            "customers": customers,
            "page":      1,
            "pageSize":  len(customers),
            "hasMore":   False,
            "total":     len(customers),
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
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
        text = content.decode("utf-8-sig")   # handles BOM from QB exports
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows   = list(reader)

    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty")

    imported = 0
    skipped  = 0

    for row in rows:
        row = {k.strip(): (v or "").strip() for k, v in row.items()}

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

        job_type   = row.get("Job Type") or row.get("Customer Type") or row.get("Type") or ""
        account_no = (
            row.get("Account No.")
            or row.get("Account Number")
            or row.get("Account #")
            or row.get("Acct No")
            or ""
        )
        terms       = row.get("Terms") or row.get("Payment Terms") or ""
        balance_str = row.get("Balance Total") or row.get("Balance") or row.get("Current Balance") or "0"
        balance_str = balance_str.replace("$", "").replace(",", "").strip()
        if balance_str.startswith("(") and balance_str.endswith(")"):
            balance_str = "-" + balance_str[1:-1]
        try:
            balance = float(balance_str) if balance_str else 0.0
        except ValueError:
            balance = 0.0

        qb_customers[normalize(name)] = {
            "name":        name,
            "accountNo":   account_no,
            "billingType": map_billing_type(job_type),
            "jobType":     job_type,
            "terms":       terms,
            "balance":     balance,
        }
        imported += 1

    _save_json(QB_DATA_FILE, qb_customers)
    print(f"[import-qb] Saved {len(qb_customers)} QB customers to {QB_DATA_FILE}")

    return {
        "success":  True,
        "message":  f"{imported} customers imported, {skipped} skipped",
        "imported": imported,
        "skipped":  skipped,
        "total":    len(qb_customers),
    }


# ─── GET /api/customers/{account_id} ──────────────────────────────────────────
# NOTE: wildcard — must stay BELOW all fixed routes above
@router.get("/customers/{account_id}")
async def get_customer(account_id: str):
    if not session_store.get("session_id"):
        raise HTTPException(status_code=401, detail="Not logged in")

    try:
        all_devices = []
        next_id     = 0

        while True:
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
            devices = result.get("result") or []
            if not devices:
                break
            # Filter to this company only (account_id is the companyId string)
            for d in devices:
                uc = d.get("userContact") or {}
                cid = str((uc.get("userCompany") or {}).get("id") or "")
                if cid == account_id:
                    all_devices.append(d)
            if len(devices) < 1000:
                break
            next_id = devices[-1].get("id", 0)

        normalized = []
        for d in all_devices:
            device = d.get("device") or {}
            normalized.append({
                "serialNumber":      device.get("serialNumber") or "",
                "deviceType":        (device.get("deviceType") or {}).get("name") or device.get("deviceType") or "",
                "activeBillingPlan": d.get("productCode") or "",
                "ratePlanCode":      d.get("productCode") or "",
                "database":          str((d.get("userContact") or {}).get("userCompany", {}).get("id") or ""),
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
