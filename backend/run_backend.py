"""
GeoBridge Backend Launcher
--------------------------
Run this instead of uvicorn directly to eliminate all path/import ambiguity.

Usage (PowerShell - from ANY directory):
    & "C:\Users\Rodney\OneDrive\Desktop\geobridge-app\.venv\Scripts\python.exe" `
        "C:\Users\Rodney\OneDrive\Desktop\geobridge-app\backend\run_backend.py"

Or if you cd to the backend directory first:
    cd C:\Users\Rodney\OneDrive\Desktop\geobridge-app\backend
    ..\.venv\Scripts\python.exe run_backend.py

This script:
1. Prints the EXACT main.py being loaded (proves which file is used)
2. Prints ALL registered routes BEFORE starting the server
3. Starts uvicorn programmatically - zero path ambiguity
"""

import sys
import os

# ── STEP 1: Force sys.path to THIS directory (backend/) ──────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

print("=" * 60)
print("  GeoBridge Backend Launcher")
print("=" * 60)
print(f"  Python:  {sys.executable}")
print(f"  CWD:     {os.getcwd()}")
print(f"  Backend: {HERE}")

main_py = os.path.join(HERE, 'main.py')
customers_py = os.path.join(HERE, 'geotab', 'customers.py')
print(f"  main.py:      {main_py}")
print(f"    size: {os.path.getsize(main_py)} bytes")
print(f"  customers.py: {customers_py}")
print(f"    size: {os.path.getsize(customers_py)} bytes")
print()

# ── STEP 2: Show last few lines of main.py (confirms router prefix) ───────────
print("  Last 5 lines of main.py:")
with open(main_py, 'r', encoding='utf-8-sig') as f:  # utf-8-sig strips BOM
    lines = f.readlines()
for line in lines[-5:]:
    print("    " + line.rstrip())
print()

# ── STEP 3: Import and dump all routes ───────────────────────────────────────
print("  Importing app...")
try:
    import main as app_module
    app = app_module.app
    print("  Registered routes:")
    for route in app.routes:
        methods = getattr(route, 'methods', {'?'})
        print(f"    {sorted(methods)} {route.path}")
except Exception as e:
    import traceback
    print(f"  IMPORT ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 60)
print("  Starting server on http://127.0.0.1:8000")
print("  Docs:    http://127.0.0.1:8000/docs")
print("=" * 60)
print()

# ── STEP 4: Start uvicorn ─────────────────────────────────────────────────────
import uvicorn

uvicorn.run(
    "main:app",
    host="127.0.0.1",
    port=8000,
    reload=False,      # reload=True re-imports from cwd, not HERE — keep False
    app_dir=HERE,
    log_level="info",
)
