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

# Allow the React frontend to talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Health Check ────────────────────────────────────────────
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

# ─── Auth Routes ─────────────────────────────────────────────
from geotab.auth import router as auth_router
app.include_router(auth_router, prefix="/api/geotab", tags=["Geotab Auth"])

# ─── Customer Routes ──────────────────────────────────────────
from geotab.customers import router as customers_router
app.include_router(customers_router, prefix="/api/geotab", tags=["Customers"])
