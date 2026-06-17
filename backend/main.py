from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env'))

app = FastAPI(
    title="GeoBridge API",
    description="GeoBridge Invoicing Suite - Backend API",
    version="1.0.0"
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
