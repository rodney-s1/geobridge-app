"""
diag_cache_structure.py
-----------------------
Inspects myadmin_cache.json and shows its top-level structure so we can
understand what field names are used and find Han-CS records.

Run from project root:
    python backend/geotab/diag_cache_structure.py
"""
import json, os, sys

_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "myadmin_cache.json"),
    os.path.join(os.path.dirname(__file__), "..", "..", "myadmin_cache.json"),
    "myadmin_cache.json",
]
cache_path = next((os.path.abspath(p) for p in _CANDIDATES if os.path.exists(p)), None)
if not cache_path:
    print("ERROR: myadmin_cache.json not found.")
    sys.exit(1)

print(f"Cache: {cache_path}")
print(f"Size:  {os.path.getsize(cache_path):,} bytes\n")

with open(cache_path, encoding="utf-8") as f:
    raw = json.load(f)

def show_dict(d, indent=0):
    pad = "  " * indent
    for k, v in list(d.items())[:12]:
        if isinstance(v, dict):
            print(f"{pad}{repr(k)}: dict({len(v)} keys: {list(v.keys())[:6]})")
        elif isinstance(v, list):
            print(f"{pad}{repr(k)}: list[{len(v)}]", end="")
            if v and isinstance(v[0], dict):
                print(f" -> item keys: {list(v[0].keys())[:8]}")
            else:
                print()
        else:
            print(f"{pad}{repr(k)}: {repr(v)[:80]}")

print(f"Top-level type: {type(raw).__name__}")

if isinstance(raw, dict):
    print(f"Top-level keys ({len(raw)}): {list(raw.keys())[:15]}")
    print()
    # Show structure of first value
    first_key = next(iter(raw))
    first_val = raw[first_key]
    print(f"First entry key: {repr(first_key)}")
    print(f"First entry type: {type(first_val).__name__}")
    if isinstance(first_val, dict):
        print("First entry contents:")
        show_dict(first_val, indent=1)
    elif isinstance(first_val, list) and first_val and isinstance(first_val[0], dict):
        print(f"First entry[0] keys: {list(first_val[0].keys())}")
    print()

    # Search for Han-CS anywhere in the structure
    print("Searching for '{Han-CS}' in all string values (top 3 found)...")
    found = 0
    for k, v in raw.items():
        text = json.dumps(v, default=str)
        if "{Han-CS}" in text and found < 3:
            print(f"\n  Key: {repr(k)}")
            # Find the Han-CS substring context
            idx = text.find("{Han-CS}")
            print(f"  Context: ...{text[max(0,idx-60):idx+80]}...")
            found += 1
    if found == 0:
        print("  NONE FOUND — {Han-CS} does not appear in this cache at all!")
        # Check what name patterns exist
        print("\n  Sampling company/customer name fields:")
        for k, v in list(raw.items())[:5]:
            if isinstance(v, dict):
                for name_key in ("companyName", "customerName", "name", "Company", "Customer"):
                    if name_key in v:
                        print(f"    {name_key}: {repr(v[name_key])}")
                        break

elif isinstance(raw, list):
    print(f"List length: {len(raw)}")
    if raw and isinstance(raw[0], dict):
        print(f"Item[0] keys: {list(raw[0].keys())}")
        print("\nItem[0] sample:")
        show_dict(raw[0], indent=1)
        print()
        # Search for Han-CS
        print("Searching for '{Han-CS}' in list items...")
        found = 0
        for i, item in enumerate(raw):
            text = json.dumps(item, default=str)
            if "{Han-CS}" in text and found < 3:
                print(f"\n  Index {i}:")
                idx = text.find("{Han-CS}")
                print(f"  Context: ...{text[max(0,idx-60):idx+80]}...")
                found += 1
        if found == 0:
            print("  NONE FOUND")
            # Show name field samples
            name_keys = ["companyName", "customerName", "name", "Company", "Customer"]
            for nk in name_keys:
                samples = [item[nk] for item in raw[:10] if nk in item]
                if samples:
                    print(f"\n  '{nk}' samples: {samples[:5]}")
