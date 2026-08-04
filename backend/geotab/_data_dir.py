"""
_data_dir.py — Shared helper for resolving the user-data directory.

All mutable runtime JSON files (billing overrides, QB cache, sync cache, etc.)
are written here so they survive application reinstalls.

Resolution order
----------------
1. GEOBRIDGE_DATA_DIR env var  — set by Electron (main.js) to app.getPath('userData')
                                  e.g.  C:\\Users\\Rodney\\AppData\\Roaming\\GeoBridge
2. Fallback: the same directory as this file (__file__'s parent)
             — used in development / direct Python invocation

First-run migration
-------------------
When the env var IS set and the data dir is different from _HERE, this module
copies any mutable JSON files that already exist at the old _HERE location into
the new data dir (only if they don't already exist there).  This preserves user
settings created by an older install that used the in-bundle paths.

After migration the old copies are left in place (harmless) so a downgrade keeps
working, but the app will always read/write from _DATA_DIR going forward.
"""

import os
import shutil

# Directory of this source file (inside the bundle / repo)
_HERE = os.path.dirname(os.path.abspath(__file__))

# ── Resolve data directory ────────────────────────────────────────────────────
_env_dir = os.environ.get("GEOBRIDGE_DATA_DIR", "").strip()

if _env_dir:
    _DATA_DIR = _env_dir
    # Create the directory tree on first launch
    os.makedirs(_DATA_DIR, exist_ok=True)
else:
    # Dev / unpackaged fallback — keep the old behaviour
    _DATA_DIR = _HERE

# ── First-run migration ───────────────────────────────────────────────────────
# The complete list of mutable files that used to live in _HERE.
# If the env var is set (packaged app) and the file exists at the old location
# but NOT at the new location, copy it over once.

_MUTABLE_FILES = [
    # customers.py
    "qb_customers.json",
    "billing_overrides.json",
    "billing_type_overrides.json",
    "billing_date_overrides.json",
    "first_connect_date_overrides.json",
    "billing_frequency_overrides.json",
    "myadmin_cache.json",
    "contract_checkpoint.json",
    "qb_last_import_columns.json",
    # invoices.py
    "excluded_invoices.json",
    "invoice_sku_overrides.json",
    # settings.py  (user-editable through the UI)
    "sku_catalog.json",
    "sku_mappings.json",
    "customer_rate_plan_mappings.json",
    "sku_customer_overrides.json",
    "qb_invoice_quantities.json",
    "serial_prefix_mappings.json",
    # qb_sync.py
    "qb_sync_history.json",
]

if _env_dir and _DATA_DIR != _HERE:
    _migrated = []
    for _fname in _MUTABLE_FILES:
        _src = os.path.join(_HERE, _fname)
        _dst = os.path.join(_DATA_DIR, _fname)
        if os.path.exists(_src) and not os.path.exists(_dst):
            try:
                shutil.copy2(_src, _dst)
                _migrated.append(_fname)
            except Exception as _exc:
                print(f"[data_dir] WARNING: could not migrate {_fname}: {_exc}")
    if _migrated:
        print(f"[data_dir] Migrated {len(_migrated)} file(s) from bundle to "
              f"user-data dir: {_migrated}")
    else:
        print(f"[data_dir] User-data dir ready (no migration needed): {_DATA_DIR}")
else:
    print(f"[data_dir] Using bundle dir for data (dev mode): {_DATA_DIR}")
