"""
diag_han_cs_plans.py
--------------------
Reads myadmin_cache.json (same format reconciliation.py uses after _build_company_map)
and shows exactly what billingPlan / promoCode values exist on Han-CS devices,
plus the count that would pass the HANOVER+GO gate.

Run from the project root:
    python backend/geotab/diag_han_cs_plans.py

The cache file is looked up in the same places customers.py uses.
"""
import json
import os
import sys
import collections

# ── locate cache file ──────────────────────────────────────────────────────────
_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "myadmin_cache.json"),
    os.path.join(os.path.dirname(__file__), "..", "..", "myadmin_cache.json"),
]
cache_path = None
for p in _CANDIDATES:
    if os.path.exists(p):
        cache_path = os.path.abspath(p)
        break

if cache_path is None:
    # Try CWD
    if os.path.exists("myadmin_cache.json"):
        cache_path = os.path.abspath("myadmin_cache.json")
    else:
        print("ERROR: myadmin_cache.json not found. Run from project root or place next to this script.")
        sys.exit(1)

print(f"Reading cache: {cache_path}\n")

with open(cache_path, encoding="utf-8") as f:
    raw = json.load(f)

# Cache may be wrapped in {"data": [...]} or {"companies": {...}} depending on version
if isinstance(raw, list):
    contracts = raw
elif isinstance(raw, dict):
    # Try common wrapper keys
    contracts = (
        raw.get("contracts")
        or raw.get("data")
        or []
    )
    if not contracts:
        # Might be a dict of {cid: {...}} (company_map style)
        # Check if values look like company records
        first = next(iter(raw.values()), {}) if raw else {}
        if isinstance(first, dict) and "customerName" in first:
            # Already company_map format — iterate directly
            contracts = None  # handled below
        else:
            print("ERROR: Unrecognised cache format.")
            sys.exit(1)
else:
    print("ERROR: Unrecognised cache format.")
    sys.exit(1)

# ── colon-strip (mirrors reconciliation.py line 420-421) ──────────────────────
def strip_plan(p: str) -> str:
    p = (p or "").strip()
    if ":" in p:
        p = p.split(":")[0].strip()
    return p

# ── process contracts list format ─────────────────────────────────────────────
plan_counts    = collections.Counter()
promo_counts   = collections.Counter()
go_non_hanover = collections.Counter()  # billingPlan==GO but promoCode != HANOVER
hanover_non_go = collections.Counter()  # promoCode==HANOVER but billingPlan!=GO

total_active    = 0
total_han_cs    = 0
qualifying      = 0   # promoCode==HANOVER AND NOT never_activated AND billingPlan(stripped)==GO

if contracts is not None:
    # List-of-contract-records format from myadmin_cache.json
    for c in contracts:
        cname = c.get("companyName") or c.get("customerName") or ""
        if "{Han-CS}" not in cname:
            continue

        promo_raw  = (c.get("promoCode") or "").upper().strip()
        adp        = c.get("activeDevicePlan") or {}
        plan_raw   = strip_plan(adp.get("name") or c.get("billingPlan") or "")
        plan_upper = plan_raw.upper()

        adp_upper = plan_upper
        is_never  = (
            adp_upper == "NEVER ACTIVATED"
            or adp_upper == ""
            or "never" in adp_upper
        )

        total_han_cs += 1
        if is_never:
            continue
        total_active += 1

        plan_counts[plan_raw] += 1
        promo_counts[promo_raw] += 1

        if promo_raw == "HANOVER" and plan_upper == "GO":
            qualifying += 1
        elif plan_upper == "GO" and promo_raw != "HANOVER":
            go_non_hanover[promo_raw] += 1
        elif promo_raw == "HANOVER" and plan_upper != "GO":
            hanover_non_go[plan_raw] += 1
else:
    # company_map dict format  {pkey: {customerName, devices: [...]}}
    for _pkey, cdata in raw.items():
        cname = cdata.get("customerName", "")
        if "{Han-CS}" not in cname:
            continue
        for dev in cdata.get("devices", []):
            plan_raw   = strip_plan(dev.get("billingPlan") or "")
            plan_upper = plan_raw.upper()
            promo_raw  = (dev.get("promoCode") or "").upper().strip()
            is_never   = dev.get("neverActivated", False)

            total_han_cs += 1
            if is_never:
                continue
            total_active += 1

            plan_counts[plan_raw] += 1
            promo_counts[promo_raw] += 1

            if promo_raw == "HANOVER" and plan_upper == "GO":
                qualifying += 1
            elif plan_upper == "GO" and promo_raw != "HANOVER":
                go_non_hanover[promo_raw] += 1
            elif promo_raw == "HANOVER" and plan_upper != "GO":
                hanover_non_go[plan_raw] += 1

# ── report ─────────────────────────────────────────────────────────────────────
print(f"Total {'{Han-CS}'} device records:  {total_han_cs}")
print(f"  Active (not never-activated):    {total_active}")
print(f"  HANOVER+GO qualifying:           {qualifying}")
print()
print("billingPlan breakdown (active Han-CS devices, after colon-strip):")
for p, c in plan_counts.most_common(30):
    marker = " ← COUNTED" if p.upper() == "GO" else ""
    print(f"  {repr(p):35s} {c:5d}{marker}")
print()
print("promoCode breakdown (active Han-CS devices):")
for p, c in promo_counts.most_common(15):
    marker = " ← COUNTED" if p == "HANOVER" else ""
    print(f"  {repr(p):35s} {c:5d}{marker}")
print()

if go_non_hanover:
    print("GO-plan devices EXCLUDED (promoCode != HANOVER):")
    for p, c in go_non_hanover.most_common():
        print(f"  promoCode={repr(p):25s} {c:5d}")
    print()

if hanover_non_go:
    print("HANOVER-promo devices EXCLUDED (billingPlan != GO):")
    for p, c in hanover_non_go.most_common():
        print(f"  billingPlan={repr(p):25s} {c:5d}")
    print()

print(f"Summary: {qualifying} devices qualify (HANOVER promo + active + GO plan).")
print(f"  Gap from 1404 raw Tier-5 count: {1404 - qualifying} devices excluded by gate.")
