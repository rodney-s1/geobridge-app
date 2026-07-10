from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

# Load .env from the backend directory (where credentials live)
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_BACKEND_DIR, ".env"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup: kick off background device-DB refresh ----------------------
    from geotab.customers import start_background_refresh
    start_background_refresh()
    yield
    # --- Shutdown (nothing to clean up) --------------------------------------


app = FastAPI(
    title="GeoBridge API",
    description="GeoBridge Invoicing Suite - Backend API",
    version="1.0.0",
    lifespan=lifespan,
    # Disable public Swagger/ReDoc UI — this is a local desktop app;
    # the API schema should not be browsable without intentional enablement.
    docs_url=None,
    redoc_url=None,
)

# Allow the React frontend to talk to this backend.
# Electron loads the built frontend as file:// which sends Origin: null.
# Dev mode uses http://localhost:5173 (Vite) as the origin.
# This is a local desktop-only app -- open CORS is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # must be False when allow_origins=["*"]
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept", "Origin",
                   "X-Requested-With", "Access-Control-Request-Method",
                   "Access-Control-Request-Headers"],
    expose_headers=["*"],
    max_age=3600,
)

# Health Check
@app.get("/")
def root():
    return {"status": "ok", "message": "GeoBridge API is running"}

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "app": "GeoBridge",
        "version": "1.0.0"
    }

# Auth Routes
from geotab.auth import router as auth_router
app.include_router(auth_router, prefix="/api/geotab", tags=["Geotab Auth"])

# Customer Routes
from geotab.customers import router as customers_router
app.include_router(customers_router, prefix="/api", tags=["Customers"])

# Settings Routes (QB SKU / Rate-Plan mapping)
from geotab.settings import router as settings_router
app.include_router(settings_router, prefix="/api", tags=["Settings"])

# Reconciliation Routes
from geotab.reconciliation import router as reconciliation_router
app.include_router(reconciliation_router, prefix="/api", tags=["Reconciliation"])

# Invoice Routes
from geotab.invoices import router as invoices_router
app.include_router(invoices_router, prefix="/api", tags=["Invoices"])

# Activations Routes
from geotab.activations import router as activations_router
app.include_router(activations_router, prefix="/api", tags=["Activations"])

from geotab.reports import router as reports_router
app.include_router(reports_router, prefix="/api", tags=["Reports"])

# ============================================================
#  S3 BACKUP  endpoints + startup restore
# ============================================================
from fastapi import APIRouter as _APIRouter, Depends
from geotab.auth import require_session

_s3_router = _APIRouter(dependencies=[Depends(require_session)])

@_s3_router.get("/settings/s3-status")
def s3_status():
    """Return S3 sync status for each data file."""
    try:
        from geotab.s3_sync import get_status
        return {"ok": True, "files": get_status()}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "files": []}


@_s3_router.post("/settings/s3-backup")
def s3_backup_now():
    """Force an immediate upload of all data files to S3."""
    try:
        from geotab.s3_sync import backup_all
        results = backup_all()
        uploaded = sum(1 for v in results.values() if v)
        return {"ok": True, "uploaded": uploaded, "results": results}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


app.include_router(_s3_router, prefix="/api", tags=["S3 Backup"])


# S3 restore on startup -- download any missing data files from S3
try:
    from geotab.s3_sync import restore_missing
    _restore_results = restore_missing()
    _restored = [k for k, v in _restore_results.items() if v == "restored"]
    if _restored:
        print(f"[main] S3 restore: pulled {len(_restored)} missing file(s): {_restored}")
    else:
        print("[main] S3 restore: all data files already present locally")
except Exception as _exc:
    print(f"[main] S3 restore skipped: {_exc}")
