"""
diag_han_cs_plans.py
--------------------
Reads myadmin_cache.json and audits Han-CS device billingPlan / promoCode
values using the EXACT same field access pattern as reconciliation.py.

Shows:
  - Total active Han-CS contracts
  - How many pass the HANOVER+GO gate
  - Full billingPlan and promoCode breakdown
  - Why non-qualifying devices are excluded

Run from project root:
    python backend/geotab/diag_han_cs_plans.py
"""
import json, os, sys, html, collections, datetime, time

# ── locate cache ───────────────────────────────────────────────────────────────
_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "myadmin_cache.json"),
    os.path.join(os.path.dirname(__file__), "..", "..", "myadmin_cache.json"),
    "myadmin_cache.json",
]
cache_path = next((os.path.abspath(p) for p in _CANDIDATES if os.path.exists(p)), None)
if not cache_path:
    print("ERROR: myadmin_cache.json not found.")
    sys.exit(1)

print(f"Reading cache: {cache_path}")
print(f"Size: {os.path.getsize(cache_path):,} bytes\n")

with open(cache_path, encoding="utf-8") as f:
    raw = json.load(f)

contracts = raw.get("contracts") or []

# Print cache timestamps so we can verify this file matches in-memory reconciliation data
_now = time.time()
for ts_key in ("fetched_at", "device_db_refreshed_at", "customer_fetched_at"):
    ts = raw.get(ts_key)
    if ts:
        dt_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        age_h  = (_now - ts) / 3600
        print(f"  {ts_key:<30s}: {dt_str}  ({age_h:.2f}h ago)")
print()
print(f"Total contract records: {len(contracts):,}")

# ── colon-strip (mirrors reconciliation.py lines 420-421) ─────────────────────
def strip_plan(p):
    p = (p or "").strip()
    if ":" in p:
        p = p.split(":")[0].strip()
    return p

# ── never-activated detection (mirrors reconciliation.py lines 425-430) ───────
def is_never_activated(billing_plan_stripped):
    u = billing_plan_stripped.upper()
    return u == "NEVER ACTIVATED" or u == "" or "never" in u

# ── scan contracts ─────────────────────────────────────────────────────────────
plan_counts    = collections.Counter()
promo_counts   = collections.Counter()
hanover_non_go = collections.Counter()   # HANOVER promo but plan != GO
go_non_hanover = collections.Counter()   # GO plan but promo != HANOVER
neither        = collections.Counter()   # Han-CS active, neither gate passes

total_contracts  = 0
terminated       = 0
total_han_cs     = 0
never_act        = 0
qualifying       = 0

# For per-customer breakdown
cust_qualify     = collections.Counter()
cust_total       = collections.Counter()

for c in contracts:
    total_contracts += 1

    if c.get("isTerminated"):
        terminated += 1
        continue

    uc      = c.get("userContact") or {}
    company = uc.get("userCompany") or {}
    cname   = html.unescape(company.get("name") or "").strip()

    if "{Han-CS}" not in cname:
        continue

    total_han_cs += 1

    promo_raw    = (c.get("promoCode") or "").upper().strip()
    adp          = c.get("activeDevicePlan") or {}
    plan_raw     = strip_plan(adp.get("name") or "")
    plan_upper   = plan_raw.upper()

    if is_never_activated(plan_raw):
        never_act += 1
        continue

    # Active Han-CS device
    cust_total[cname] += 1
    plan_counts[plan_raw] += 1
    promo_counts[promo_raw] += 1

    if promo_raw == "HANOVER" and plan_upper == "GO":
        qualifying += 1
        cust_qualify[cname] += 1
    elif promo_raw == "HANOVER" and plan_upper != "GO":
        hanover_non_go[plan_raw] += 1
    elif plan_upper == "GO" and promo_raw != "HANOVER":
        go_non_hanover[promo_raw] += 1
    else:
        neither[f"promo={promo_raw!r} plan={plan_raw!r}"] += 1

active_han_cs = total_han_cs - never_act

# ── report ─────────────────────────────────────────────────────────────────────
print(f"Terminated (skipped):            {terminated:,}")
print(f"Total Han-CS contracts:          {total_han_cs:,}")
print(f"  Never-activated (skipped):     {never_act:,}")
print(f"  Active Han-CS contracts:       {active_han_cs:,}")
print(f"  HANOVER+GO qualifying:         {qualifying:,}")
print(f"  Gap (active - qualifying):     {active_han_cs - qualifying:,}")
print()

print("─── billingPlan breakdown (active Han-CS, after colon-strip) ───────────")
for p, c in plan_counts.most_common(30):
    marker = "  ← QUALIFIES (part of gate)" if p.upper() == "GO" else ""
    print(f"  {repr(p):40s} {c:5d}{marker}")
print()

print("─── promoCode breakdown (active Han-CS) ────────────────────────────────")
for p, c in promo_counts.most_common(20):
    marker = "  ← QUALIFIES (part of gate)" if p == "HANOVER" else ""
    print(f"  {repr(p):40s} {c:5d}{marker}")
print()

if hanover_non_go:
    print("─── EXCLUDED: HANOVER promo but billingPlan != GO ──────────────────────")
    for p, c in hanover_non_go.most_common():
        print(f"  billingPlan={repr(p):35s} {c:5d}")
    print()

if go_non_hanover:
    print("─── EXCLUDED: GO plan but promoCode != HANOVER ─────────────────────────")
    for p, c in go_non_hanover.most_common():
        print(f"  promoCode={repr(p):37s} {c:5d}")
    print()

if neither:
    print("─── EXCLUDED: neither HANOVER promo nor GO plan ────────────────────────")
    for combo, c in neither.most_common(15):
        print(f"  {combo:50s} {c:5d}")
    print()

print(f"SUMMARY: {qualifying} of {active_han_cs} active Han-CS devices qualify "
      f"(HANOVER promo + GO plan).")
print(f"  Raw Tier-5 count (all active):  {active_han_cs}")
print(f"  Gated count (HANOVER+GO):        {qualifying}")
print(f"  Excluded by gate:                {active_han_cs - qualifying}")
print()

# ── per-customer breakdown ─────────────────────────────────────────────────────
print("─── Per-customer: total active vs qualifying ───────────────────────────")
print(f"  {'Customer name':<55} {'Active':>6}  {'Qualif':>6}")
print(f"  {'-'*55} {'------':>6}  {'------':>6}")
for cname in sorted(cust_total.keys()):
    t = cust_total[cname]
    q = cust_qualify.get(cname, 0)
    marker = " ← GAP" if q < t else ""
    print(f"  {cname:<55} {t:6d}  {q:6d}{marker}")
print()
print(f"  TOTALS: {sum(cust_total.values())} active, {sum(cust_qualify.values())} qualifying")
