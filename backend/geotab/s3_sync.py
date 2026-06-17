"""
s3_sync.py — GeoBridge S3 backup/restore helpers
-------------------------------------------------
All five data files are mirrored to S3 under the key prefix  "data/".

Upload  : called after every settings save (fire-and-forget thread)
Restore : called at startup — downloads any file that is missing locally
Backup  : uploads ALL files right now (used by /api/settings/s3-backup)
Status  : returns last-modified timestamps for each file (used by the UI)
"""

import json
import logging
import os
import threading
from datetime import timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — read from environment (set by main.py via python-dotenv)
# ---------------------------------------------------------------------------
_BUCKET  = os.environ.get("S3_BUCKET",            "geobridge-data-backup")
_REGION  = os.environ.get("AWS_REGION",            "us-east-1")
_KEY_ID  = os.environ.get("AWS_ACCESS_KEY_ID",     "")
_SECRET  = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

# Files we care about — relative name -> absolute path resolved at import time
_HERE = Path(__file__).parent

DATA_FILES: dict[str, Path] = {
    "sku_catalog.json":                   _HERE / "sku_catalog.json",
    "sku_mappings.json":                  _HERE / "sku_mappings.json",
    "sku_customer_overrides.json":        _HERE / "sku_customer_overrides.json",
    "customer_rate_plan_mappings.json":   _HERE / "customer_rate_plan_mappings.json",
    "geotab_cache.json":                  _HERE / "geotab_cache.json",
}

_S3_PREFIX = "data/"


def _client():
    """Return a fresh boto3 S3 client (lightweight — no connection pooling needed)."""
    import boto3
    return boto3.client(
        "s3",
        aws_access_key_id=_KEY_ID,
        aws_secret_access_key=_SECRET,
        region_name=_REGION,
    )


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _s3_key(filename: str) -> str:
    return f"{_S3_PREFIX}{filename}"


def upload_file(filename: str) -> bool:
    """Upload a single data file to S3. Returns True on success."""
    path = DATA_FILES.get(filename)
    if path is None:
        logger.warning("s3_sync: unknown file '%s' — skipped", filename)
        return False
    if not path.exists():
        logger.debug("s3_sync: '%s' does not exist locally — skipped", filename)
        return False
    try:
        _client().upload_file(
            str(path),
            _BUCKET,
            _s3_key(filename),
            ExtraArgs={"ContentType": "application/json"},
        )
        logger.info("s3_sync: uploaded %s -> s3://%s/%s", filename, _BUCKET, _s3_key(filename))
        return True
    except Exception as exc:
        logger.error("s3_sync: upload failed for '%s': %s", filename, exc)
        return False


def upload_file_async(filename: str) -> None:
    """Fire-and-forget upload so the API response is never delayed."""
    threading.Thread(target=upload_file, args=(filename,), daemon=True).start()


def download_file(filename: str) -> bool:
    """Download a single file from S3 to its local path. Returns True on success."""
    path = DATA_FILES.get(filename)
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _client().download_file(_BUCKET, _s3_key(filename), str(path))
        logger.info("s3_sync: restored %s from s3://%s/%s", filename, _BUCKET, _s3_key(filename))
        return True
    except Exception as exc:
        # NoSuchKey is normal for first run — debug level only
        level = logging.DEBUG if "NoSuchKey" in str(exc) or "404" in str(exc) else logging.WARNING
        logger.log(level, "s3_sync: download skipped for '%s': %s", filename, exc)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def restore_missing() -> dict[str, str]:
    """
    Called at startup. For each data file that does NOT exist locally,
    attempt to download it from S3.
    Returns a dict of {filename: "restored" | "skipped" | "missing_in_s3"}.
    """
    results: dict[str, str] = {}
    for filename, path in DATA_FILES.items():
        if path.exists():
            results[filename] = "skipped"          # already on disk
        else:
            ok = download_file(filename)
            results[filename] = "restored" if ok else "missing_in_s3"
    return results


def backup_all() -> dict[str, bool]:
    """
    Upload every data file that exists locally.
    Returns {filename: True/False}.
    """
    results: dict[str, bool] = {}
    for filename in DATA_FILES:
        results[filename] = upload_file(filename)
    return results


def get_status() -> list[dict]:
    """
    Return S3 metadata for each data file:
    [{ "filename", "exists_locally", "s3_last_modified", "s3_size_bytes" }, ...]
    """
    s3 = _client()
    rows = []
    for filename, path in DATA_FILES.items():
        row: dict = {
            "filename":       filename,
            "exists_locally": path.exists(),
            "s3_last_modified": None,
            "s3_size_bytes":    None,
        }
        try:
            head = s3.head_object(Bucket=_BUCKET, Key=_s3_key(filename))
            lm = head["LastModified"]
            # Ensure UTC-aware then convert to ISO string
            if lm.tzinfo is None:
                lm = lm.replace(tzinfo=timezone.utc)
            row["s3_last_modified"] = lm.astimezone(timezone.utc).isoformat()
            row["s3_size_bytes"]    = head["ContentLength"]
        except Exception:
            pass   # file not yet in S3
        rows.append(row)
    return rows
