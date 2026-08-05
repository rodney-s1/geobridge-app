"""
s3_sync.py — GeoBridge S3 shared-data sync
-------------------------------------------
All mutable data files are mirrored to S3 under the key prefix "data/".
Credentials are stored in %APPDATA%\\geobridge-app\\aws_config.json (never
bundled in the installer).

Sync rules
----------
- SHARED files  : pulled on startup (S3 wins if newer), pushed on every save
- LOCAL files   : never synced (cache, checkpoint, history)
- admins.json   : stored in S3 at data/admins.json — controls who can write
                  admin-only shared files

Admin system
------------
- admins.json contains {"admins": ["developers@bluearrowmail.com", ...]}
- is_admin(username) checks this list
- Non-admins can still read all data; write endpoints check is_admin() and
  return 403 if the user is not in the list

File tiers
----------
ADMIN_ONLY  — billing/SKU config that only admins should change
ALL_USERS   — QB invoice data that any user can push after a QB import
LOCAL_ONLY  — never synced (cache is per-machine and too large/volatile)
"""

import json
import logging
import os
import threading
import time
from datetime import timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config file location
# ---------------------------------------------------------------------------
_env_dir = os.environ.get("GEOBRIDGE_DATA_DIR", "").strip()
_DATA_DIR = Path(_env_dir) if _env_dir else Path(__file__).parent
_CONFIG_FILE = _DATA_DIR / "aws_config.json"

# ---------------------------------------------------------------------------
# File tiers
# ---------------------------------------------------------------------------
# Admin-only: only admins can push these to S3
ADMIN_ONLY_FILES = {
    "billing_overrides.json",
    "billing_type_overrides.json",
    "billing_date_overrides.json",
    "first_connect_date_overrides.json",
    "billing_frequency_overrides.json",
    "sku_catalog.json",
    "sku_mappings.json",
    "customer_rate_plan_mappings.json",
    "sku_customer_overrides.json",
    "serial_prefix_mappings.json",
}

# All users can push these
ALL_USER_FILES = {
    "qb_customers.json",
    "qb_invoice_quantities.json",
    "excluded_invoices.json",
    "invoice_sku_overrides.json",
}

# Never synced — local only
LOCAL_ONLY_FILES = {
    "myadmin_cache.json",
    "contract_checkpoint.json",
    "qb_sync_history.json",
    "qb_last_import_columns.json",
}

SHARED_FILES = ADMIN_ONLY_FILES | ALL_USER_FILES

# Special S3 key for admin list
_ADMINS_S3_KEY = "data/admins.json"

# Default admin — always present even if admins.json missing from S3
_DEFAULT_ADMIN = "developers@bluearrowmail.com"

# ---------------------------------------------------------------------------
# In-memory sync state (read by SyncStatus component via /api/s3/status)
# ---------------------------------------------------------------------------
_sync_state = {
    "configured":    False,   # aws_config.json exists and is valid
    "last_sync":     None,    # ISO timestamp of last successful pull
    "last_push":     None,    # ISO timestamp of last successful push
    "last_error":    None,    # last error message or None
    "syncing":       False,   # pull in progress
    "pushing":       False,   # push in progress
}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> Optional[Dict]:
    """Load aws_config.json from AppData. Returns None if missing/invalid."""
    try:
        if _CONFIG_FILE.exists():
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            if all(k in data for k in ("accessKeyId", "secretAccessKey", "region", "bucket")):
                return data
    except Exception as e:
        logger.warning("s3_sync: failed to load aws_config.json: %s", e)
    return None


def save_config(access_key_id: str, secret_access_key: str,
                region: str = "us-east-1",
                bucket: str = "geobridge-data-backup",
                prefix: str = "data/") -> None:
    """Save AWS credentials to AppData. Called from the setup wizard."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(
        json.dumps({
            "accessKeyId":     access_key_id,
            "secretAccessKey": secret_access_key,
            "region":          region,
            "bucket":          bucket,
            "prefix":          prefix,
        }, indent=2),
        encoding="utf-8"
    )
    _sync_state["configured"] = True
    logger.info("s3_sync: aws_config.json saved to %s", _CONFIG_FILE)


def is_configured() -> bool:
    """Return True if aws_config.json exists with valid keys."""
    return _load_config() is not None


# ---------------------------------------------------------------------------
# S3 client factory
# ---------------------------------------------------------------------------

def _client(cfg: Optional[Dict] = None):
    """Return a boto3 S3 client using config file credentials."""
    import boto3
    cfg = cfg or _load_config()
    if not cfg:
        raise RuntimeError("S3 not configured — aws_config.json missing or invalid")
    return boto3.client(
        "s3",
        aws_access_key_id=cfg["accessKeyId"],
        aws_secret_access_key=cfg["secretAccessKey"],
        region_name=cfg.get("region", "us-east-1"),
    )


def _s3_key(filename: str, cfg: Optional[Dict] = None) -> str:
    cfg = cfg or _load_config() or {}
    prefix = cfg.get("prefix", "data/")
    if not prefix.endswith("/"):
        prefix += "/"
    return f"{prefix}{filename}"


def _bucket(cfg: Optional[Dict] = None) -> str:
    cfg = cfg or _load_config() or {}
    return cfg.get("bucket", "geobridge-data-backup")


# ---------------------------------------------------------------------------
# Admin management
# ---------------------------------------------------------------------------

def _load_admins_from_s3(cfg: Optional[Dict] = None) -> list:
    """Pull admins.json from S3. Returns list of admin usernames."""
    try:
        s3 = _client(cfg)
        obj = s3.get_object(Bucket=_bucket(cfg), Key=_ADMINS_S3_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        admins = data.get("admins", [])
        if _DEFAULT_ADMIN not in admins:
            admins.append(_DEFAULT_ADMIN)
        return admins
    except Exception as e:
        if "NoSuchKey" in str(e) or "404" in str(e):
            # First run — return default admin list
            return [_DEFAULT_ADMIN]
        logger.warning("s3_sync: could not load admins.json: %s", e)
        return [_DEFAULT_ADMIN]


def save_admins_to_s3(admins: list, cfg: Optional[Dict] = None) -> bool:
    """Push admins.json to S3. Only callable by existing admins."""
    try:
        if _DEFAULT_ADMIN not in admins:
            admins = [_DEFAULT_ADMIN] + admins
        payload = json.dumps({"admins": admins}, indent=2).encode("utf-8")
        s3 = _client(cfg)
        s3.put_object(
            Bucket=_bucket(cfg),
            Key=_ADMINS_S3_KEY,
            Body=payload,
            ContentType="application/json",
        )
        logger.info("s3_sync: admins.json updated — %d admins", len(admins))
        return True
    except Exception as e:
        logger.error("s3_sync: failed to save admins.json: %s", e)
        return False


# Cache admin list in memory to avoid S3 call on every request
_admins_cache: list = []
_admins_cache_time: float = 0.0
_ADMINS_CACHE_TTL = 300  # 5 minutes


def get_admins(cfg: Optional[Dict] = None) -> list:
    """Return the admin list, using a short-lived cache."""
    global _admins_cache, _admins_cache_time
    if time.time() - _admins_cache_time > _ADMINS_CACHE_TTL or not _admins_cache:
        _admins_cache = _load_admins_from_s3(cfg)
        _admins_cache_time = time.time()
    return _admins_cache


def invalidate_admins_cache() -> None:
    global _admins_cache_time
    _admins_cache_time = 0.0


def is_admin(username: str, cfg: Optional[Dict] = None) -> bool:
    """Return True if username is in the admin list."""
    if not username:
        return False
    username = username.strip().lower()
    return any(a.strip().lower() == username for a in get_admins(cfg))


# ---------------------------------------------------------------------------
# Core upload / download
# ---------------------------------------------------------------------------

def upload_file(filename: str, cfg: Optional[Dict] = None) -> bool:
    """Upload a single data file to S3. Returns True on success."""
    if filename not in SHARED_FILES:
        logger.debug("s3_sync: '%s' is local-only — skipped", filename)
        return False
    local_path = _DATA_DIR / filename
    if not local_path.exists():
        logger.debug("s3_sync: '%s' does not exist locally — skipped", filename)
        return False
    try:
        cfg = cfg or _load_config()
        if not cfg:
            return False
        _client(cfg).upload_file(
            str(local_path),
            _bucket(cfg),
            _s3_key(filename, cfg),
            ExtraArgs={"ContentType": "application/json"},
        )
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _sync_state["last_push"] = now
        _sync_state["last_error"] = None
        logger.info("s3_sync: pushed %s", filename)
        return True
    except Exception as e:
        _sync_state["last_error"] = str(e)
        logger.error("s3_sync: upload failed for '%s': %s", filename, e)
        return False


def upload_file_async(filename: str) -> None:
    """Fire-and-forget upload — never blocks the API response."""
    def _run():
        _sync_state["pushing"] = True
        try:
            upload_file(filename)
        finally:
            _sync_state["pushing"] = False
    threading.Thread(target=_run, daemon=True).start()


def download_file(filename: str, cfg: Optional[Dict] = None,
                  force: bool = False) -> bool:
    """
    Download a file from S3 to local AppData.
    If force=False (default), only overwrites if S3 version is newer.
    Returns True on success.
    """
    if filename not in SHARED_FILES:
        return False
    local_path = _DATA_DIR / filename
    try:
        cfg = cfg or _load_config()
        if not cfg:
            return False
        s3 = _client(cfg)
        key = _s3_key(filename, cfg)
        head = s3.head_object(Bucket=_bucket(cfg), Key=key)
        s3_mtime = head["LastModified"].timestamp()

        if not force and local_path.exists():
            local_mtime = local_path.stat().st_mtime
            if local_mtime >= s3_mtime:
                logger.debug("s3_sync: '%s' is up to date — skipped", filename)
                return True  # already current

        local_path.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(_bucket(cfg), key, str(local_path))
        logger.info("s3_sync: pulled %s (S3 was newer)", filename)
        return True
    except Exception as e:
        if "NoSuchKey" in str(e) or "404" in str(e):
            logger.debug("s3_sync: '%s' not in S3 yet", filename)
        else:
            logger.warning("s3_sync: download failed for '%s': %s", filename, e)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pull_all(force: bool = False) -> Dict[str, str]:
    """
    Pull all shared files from S3 (newer-wins unless force=True).
    Returns {filename: "updated" | "current" | "missing" | "error"}.
    Called at app startup and on the background 5-min timer.
    """
    _sync_state["syncing"] = True
    results: Dict[str, str] = {}
    cfg = _load_config()
    if not cfg:
        _sync_state["syncing"] = False
        _sync_state["configured"] = False
        return {f: "not_configured" for f in SHARED_FILES}

    _sync_state["configured"] = True
    try:
        for filename in sorted(SHARED_FILES):
            local_path = _DATA_DIR / filename
            try:
                key = _s3_key(filename, cfg)
                s3 = _client(cfg)
                try:
                    head = s3.head_object(Bucket=_bucket(cfg), Key=key)
                    s3_mtime = head["LastModified"].timestamp()
                except Exception as e:
                    if "NoSuchKey" in str(e) or "404" in str(e):
                        results[filename] = "missing"
                    else:
                        results[filename] = "error"
                    continue

                if not force and local_path.exists():
                    local_mtime = local_path.stat().st_mtime
                    if local_mtime >= s3_mtime:
                        results[filename] = "current"
                        continue

                local_path.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(_bucket(cfg), key, str(local_path))
                results[filename] = "updated"
                logger.info("s3_sync: pulled %s", filename)

            except Exception as e:
                results[filename] = "error"
                logger.error("s3_sync: pull failed for '%s': %s", filename, e)

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _sync_state["last_sync"] = now
        _sync_state["last_error"] = None
    except Exception as e:
        _sync_state["last_error"] = str(e)
    finally:
        _sync_state["syncing"] = False

    return results


def push_all() -> Dict[str, bool]:
    """Upload every shared file that exists locally. Used by admin backup."""
    results: Dict[str, bool] = {}
    for filename in sorted(SHARED_FILES):
        results[filename] = upload_file(filename)
    return results


def test_connection(access_key_id: str, secret_access_key: str,
                    region: str = "us-east-1",
                    bucket: str = "geobridge-data-backup") -> Dict:
    """
    Validate credentials by attempting a lightweight S3 operation.
    Returns {"ok": True} or {"ok": False, "error": "..."}.
    Used by the setup wizard before saving config.
    """
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        # head_bucket is the cheapest auth test — just checks we can reach the bucket
        s3.head_bucket(Bucket=bucket)
        return {"ok": True}
    except Exception as e:
        msg = str(e)
        if "NoSuchBucket" in msg:
            return {"ok": False, "error": f"Bucket '{bucket}' not found"}
        if "InvalidAccessKeyId" in msg or "SignatureDoesNotMatch" in msg:
            return {"ok": False, "error": "Invalid access key or secret"}
        if "AccessDenied" in msg:
            return {"ok": False, "error": "Access denied — check IAM permissions"}
        return {"ok": False, "error": msg}


def get_sync_state() -> Dict:
    """Return current in-memory sync state for the UI badge."""
    return {**_sync_state, "configured": is_configured()}


# ---------------------------------------------------------------------------
# Background sync timer — pulls every 5 minutes while app is running
# ---------------------------------------------------------------------------
_bg_timer: Optional[threading.Timer] = None


def _bg_pull():
    global _bg_timer
    try:
        if is_configured():
            results = pull_all()
            updated = [k for k, v in results.items() if v == "updated"]
            if updated:
                logger.info("s3_sync: background pull updated %d file(s): %s",
                            len(updated), ", ".join(updated))
    except Exception as e:
        logger.error("s3_sync: background pull error: %s", e)
    finally:
        # Reschedule
        _bg_timer = threading.Timer(300, _bg_pull)
        _bg_timer.daemon = True
        _bg_timer.start()


def start_background_sync() -> None:
    """Start the 5-minute background pull timer. Called from run_backend.py."""
    global _bg_timer
    if _bg_timer is not None:
        return  # already running
    _bg_timer = threading.Timer(300, _bg_pull)
    _bg_timer.daemon = True
    _bg_timer.start()
    logger.info("s3_sync: background sync timer started (every 5 min)")
