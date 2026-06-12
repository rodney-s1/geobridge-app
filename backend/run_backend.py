"""
GeoBridge Backend Launcher
--------------------------
Run this instead of uvicorn directly to eliminate all path/import ambiguity.

Usage (PowerShell - from ANY directory):
    cd C:\\Users\\Rodney\\OneDrive\\Desktop\\geobridge-app\\backend
    ..\\.venv\\Scripts\\python.exe run_backend.py
"""

import sys
import os

# Force sys.path to THIS directory (backend/)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

print("=" * 60)
print("  GeoBridge Backend Launcher")
print("=" * 60)
print("  Python:  " + sys.executable)
print("  CWD:     " + os.getcwd())
print("  Backend: " + HERE)

main_py = os.path.join(HERE, "main.py")
customers_py = os.path.join(HERE, "geotab", "customers.py")
print("  main.py:      " + main_py)
print("    size: " + str(os.path.getsize(main_py)) + " bytes")
print("  customers.py: " + customers_py)
print("    size: " + str(os.path.getsize(customers_py)) + " bytes")
print("")

# Show last 5 lines of main.py to confirm router prefix
print("  Last 5 lines of main.py:")
with open(main_py, "r", encoding="utf-8-sig") as f:
    lines = f.readlines()
for line in lines[-5:]:
    print("    " + line.rstrip())
print("")

# Import app and dump all registered routes
print("  Importing app...")
try:
    import main as app_module
    app = app_module.app
    print("  Registered routes:")
    for route in app.routes:
        methods = getattr(route, "methods", {"?"})
        print("    " + str(sorted(methods)) + " " + route.path)
except Exception as e:
    import traceback
    print("  IMPORT ERROR: " + str(e))
    traceback.print_exc()
    sys.exit(1)

print("")
print("=" * 60)
print("  Starting server on http://127.0.0.1:8000")
print("  Docs:    http://127.0.0.1:8000/docs")
print("=" * 60)
print("")

# Start uvicorn
import uvicorn

uvicorn.run(
    "main:app",
    host="127.0.0.1",
    port=8000,
    reload=False,
    app_dir=HERE,
    log_level="info",
)
