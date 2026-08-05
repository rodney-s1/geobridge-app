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

# QB Sync Routes
from geotab.qb_sync import router as qb_sync_router
app.include_router(qb_sync_router, prefix="/api", tags=["QB Sync"])

# ============================================================
#  S3 Sync endpoints (new) + startup pull + background timer
# ============================================================
from geotab.settings import s3_router, s3_auth_router
app.include_router(s3_router,      tags=["S3 Sync"])
app.include_router(s3_auth_router, tags=["S3 Sync"])

# S3 pull on startup — bring this machine up to date with shared S3 data
try:
    from geotab.s3_sync import pull_all as _s3_pull_all, is_configured as _s3_configured
    from geotab.s3_sync import start_background_sync
    if _s3_configured():
        _pull_results = _s3_pull_all()
        _updated = [k for k, v in _pull_results.items() if v == "updated"]
        print(f"[main] S3 startup pull: {len(_updated)} file(s) updated from S3")
    else:
        print("[main] S3 not yet configured — skipping startup pull")
    start_background_sync()
except Exception as _exc:
    print(f"[main] S3 startup skipped: {_exc}")
