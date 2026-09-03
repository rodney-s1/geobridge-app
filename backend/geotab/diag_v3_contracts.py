"""
diag_v3_contracts.py
---------------------
One-off diagnostic: makes a SINGLE live call to the new MyAdmin
GetDeviceContracts V3 endpoint (https://myadminapi.geotab.com/v3/MyAdminApi.ashx)
using the session token already persisted on disk by a normal GeoBridge login
(session.json) -- no password needed, just have the app logged in first.

Prints:
  1. The full raw JSON response (so we can see the *real* field names --
     the Partner Announcement's sample response was redacted/simplified and
     may not show every field, e.g. whether userContact/userCompany still
     exists under a different name).
  2. The `pagination` block (page / perPage / total) so we can confirm V3
     really does return a total count up front.
  3. A quick "does this contract look terminated / active / etc." sanity
     check against the first record, if any.

Run from the project root (with the GeoBridge app logged in to MyAdmin at
least once so session.json exists):

    python backend/geotab/diag_v3_contracts.py

Or, if GeoBridge was packaged and session.json lives in the Electron
userData folder instead of next to this script, pass it explicitly:

    python backend/geotab/diag_v3_contracts.py "C:\\Users\\YOU\\AppData\\Roaming\\GeoBridge\\session.json"

This script makes NO changes to any files and does not affect the running
app -- it is 100% read-only against the MyAdmin API.
"""
import json
import os
import sys
import urllib.request
import urllib.error

MYADMIN_ACCOUNT = "CELU01"
V3_URL = "https://myadminapi.geotab.com/v3/MyAdminApi.ashx"

# ---------------------------------------------------------------------------
# Locate session.json (same search order as _data_dir.py: env var, then
# the geotab/ folder next to this script, then an explicit CLI arg override)
# ---------------------------------------------------------------------------
_data_dir_env = os.environ.get("GEOBRIDGE_DATA_DIR", "").strip()
_CANDIDATES = [
    sys.argv[1] if len(sys.argv) > 1 else None,
    os.path.join(_data_dir_env, "session.json") if _data_dir_env else None,
    os.path.join(os.path.dirname(__file__), "session.json"),
]
session_path = next((p for p in _CANDIDATES if p and os.path.exists(p)), None)

if not session_path:
    print("ERROR: Could not find session.json.")
    print("Make sure you are logged in to GeoBridge (MyAdmin) at least once,")
    print("or pass the full path to session.json as an argument, e.g.:")
    print(r'  python backend\geotab\diag_v3_contracts.py "C:\Users\YOU\AppData\Roaming\GeoBridge\session.json"')
    sys.exit(1)

with open(session_path, encoding="utf-8") as f:
    session = json.load(f)

user_id = session.get("user_id")
session_id = session.get("session_id")
if not user_id or not session_id:
    print(f"ERROR: {session_path} did not contain a valid user_id/session_id.")
    print("Log in again in GeoBridge, then re-run this script.")
    sys.exit(1)

print(f"Using session file: {session_path}")
print(f"user_id (apiKey):   {user_id}")
print(f"session_id:         {session_id[:8]}...(truncated)")
print()

# ---------------------------------------------------------------------------
# Build the V3 request -- small perPage so the raw dump below stays readable.
# No fromDate/toDate: per the announcement, GetDeviceContracts (unpaged v2
# variant) requires a date range OR order-date-range filter, but let's find
# out empirically what V3 actually requires by trying the minimal payload
# first, and falling back to a wide date range if it errors.
# ---------------------------------------------------------------------------
def call_v3(params, pagination):
    payload = {
        "id": -1,
        "method": "GetDeviceContracts",
        "params": params,
        "pagination": pagination,
    }
    req = urllib.request.Request(
        V3_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


base_params = {
    "apiKey": user_id,
    "sessionId": session_id,
    "forAccount": MYADMIN_ACCOUNT,
}
pagination = {"page": 1, "perPage": 3}

print("=" * 70)
print("Attempt 1: minimal params (no date range), page=1 perPage=3")
print("=" * 70)
status, body = call_v3(base_params, pagination)
print(f"HTTP {status}")
print(body[:4000])
print()

try:
    parsed = json.loads(body)
except Exception:
    parsed = None

# If that failed (e.g. requires a date range), retry with a wide range.
needs_retry = (
    parsed is None
    or "error" in parsed
    or not parsed.get("result")
)
if needs_retry:
    print("=" * 70)
    print("Attempt 2: with a wide fromDate/toDate range (2000-01-01 .. today)")
    print("=" * 70)
    import datetime
    wide_params = dict(base_params)
    wide_params["fromDate"] = "2000-01-01T00:00:00.000Z"
    wide_params["toDate"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    status, body = call_v3(wide_params, pagination)
    print(f"HTTP {status}")
    print(body[:4000])
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = None

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
if not parsed:
    print("Could not parse a JSON response -- see raw output above.")
    sys.exit(1)

if "error" in parsed:
    print(f"MyAdmin returned an error: {parsed['error']}")
    sys.exit(1)

result = parsed.get("result") or []
pag = parsed.get("pagination") or {}
print(f"pagination block: {pag}")
print(f"records returned: {len(result)}")

if result:
    first = result[0]
    print()
    print("Top-level keys of first contract record:")
    print(f"  {list(first.keys())}")
    print()
    print("Full first record (pretty-printed):")
    print(json.dumps(first, indent=2)[:4000])

    # Specifically check for the fields our current code relies on.
    print()
    print("Field checks our code currently depends on:")
    print(f"  device present?        {'device' in first} -> {first.get('device')}")
    print(f"  userContact present?   {'userContact' in first}")
    print(f"  account present?       {'account' in first} -> {first.get('account')}")
    print(f"  isTerminated present?  {'isTerminated' in first} -> {first.get('isTerminated')}")
else:
    print("No records returned -- try widening the date range or check forAccount.")

print()
print("Copy/paste this entire output back to continue the V3 migration.")
