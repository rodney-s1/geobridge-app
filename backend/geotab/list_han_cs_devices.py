"""
list_han_cs_devices.py
Run from the project root: python backend/geotab/list_han_cs_devices.py
Lists every device being counted toward han_cs_myadmin_total:
  - company name contains {Han-CS}
  - promoCode == HANOVER
  - active billingPlan == GO
"""
import json
import html
from collections import defaultdict

with open("backend/geotab/myadmin_cache.json") as f:
    cache = json.load(f)

contracts = cache.get("contracts", [])
customers = defaultdict(list)

for c in contracts:
    if c.get("isTerminated"):
        continue
    company = c.get("userContact", {}).get("userCompany", {})
    name = html.unescape((company.get("name") or "").strip())
    if "{han-cs}" not in name.lower():
        continue
    promo = (c.get("promoCode") or "").strip().upper()
    if promo != "HANOVER":
        continue
    adp = c.get("activeDevicePlan") or {}
    plan = (adp.get("name") or "").strip()
    if ":" in plan:
        plan = plan.split(":")[0].strip()
    if plan.upper() != "GO":
        continue
    serial = (c.get("device", {}).get("serialNumber") or
              c.get("device", {}).get("id") or "")
    customers[name].append(serial)

total = 0
for cname in sorted(customers):
    devs = customers[cname]
    print(f"{cname}  ({len(devs)} devices)")
    for serial in devs:
        print(f"  {serial}")
    total += len(devs)

print("---")
print(f"Total qualifying devices: {total}")
