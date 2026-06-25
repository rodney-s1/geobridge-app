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
import subprocess

# Force sys.path to THIS directory (backend/)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# -- Kill any existing process on port 8001 -----------------------------------
# This ensures a fresh restart always picks up the latest code.
print("=" * 60)
print("  GeoBridge Backend Launcher")
print("=" * 60)
print("  Python:  " + sys.executable)
print("  CWD:     " + os.getcwd())
print("  Backend: " + HERE)

try:
    # Windows: find and kill PID using port 8001
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True, text=True, timeout=10
    )
    killed = []
    for line in result.stdout.splitlines():
        if ":8001 " in line and "LISTENING" in line:
            parts = line.split()
            pid = parts[-1]
            try:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, timeout=5)
                killed.append(pid)
            except Exception:
                pass
    if killed:
        print(f"  Killed old backend PID(s): {', '.join(killed)}")
        import time; time.sleep(1)   # brief pause so port is released
    else:
        print("  No existing process on port 8001")
except Exception as e:
    print(f"  (port-kill skipped: {e})")

# -- Print git commit so we know which version is running ---------------------
try:
    repo_root = os.path.dirname(HERE)
    git_hash = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=5
    ).stdout.strip()
    git_msg = subprocess.run(
        ["git", "-C", repo_root, "log", "-1", "--pretty=%s"],
        capture_output=True, text=True, timeout=5
    ).stdout.strip()
    print(f"  Git commit: {git_hash}  {git_msg}")
except Exception:
    pass

main_py      = os.path.join(HERE, "main.py")
settings_py  = os.path.join(HERE, "geotab", "settings.py")
customers_py = os.path.join(HERE, "geotab", "customers.py")
print(f"  main.py:      {os.path.getsize(main_py)} bytes")
print(f"  settings.py:  {os.path.getsize(settings_py)} bytes")
print(f"  customers.py: {os.path.getsize(customers_py)} bytes")
print("")

# Show last 5 lines of main.py to confirm router registration
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
        path = getattr(route, "path", None)
        if path is None:
            continue  # skip _IncludedRouter / Mount objects
        methods = getattr(route, "methods", {"?"})
        print("    " + str(sorted(methods)) + " " + path)
except Exception as e:
    import traceback
    print("  IMPORT ERROR: " + str(e))
    traceback.print_exc()
    sys.exit(1)

print("")
print("=" * 60)
print("  Starting server on http://127.0.0.1:8001")
print("  Docs:    http://127.0.0.1:8001/docs")
print("=" * 60)
print("")

# Start uvicorn
import uvicorn

uvicorn.run(
    "main:app",
    host="127.0.0.1",
    port=8001,
    reload=False,
    app_dir=HERE,
    log_level="info",
)
