"""
Local filesystem object storage.
Stores files under STORAGE_PATH/{path} where path is e.g. "reports/<ticket>-<uuid>.pdf".
Replaces the previous cloud-storage integration so deployment to any host
(Render / Railway / AWS / Vercel) only needs a mountable volume.
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STORAGE_ROOT = Path(os.environ.get("STORAGE_PATH", "/app/backend/storage"))


def init_storage():
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    return True


def _resolve(path: str) -> Path:
    """Resolve a storage path safely under STORAGE_ROOT."""
    # Strip leading slashes
    safe = path.lstrip("/")
    full = (STORAGE_ROOT / safe).resolve()
    if not str(full).startswith(str(STORAGE_ROOT.resolve())):
        raise ValueError(f"Path escapes storage root: {path}")
    return full


def put_object(path: str, data: bytes, content_type: str = "application/octet-stream") -> dict:
    full = _resolve(path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    return {"path": path, "size": len(data), "content_type": content_type}


def get_object(path: str):
    full = _resolve(path)
    if not full.exists():
        raise FileNotFoundError(path)
    return full.read_bytes(), None


def delete_object(path: str) -> bool:
    try:
        full = _resolve(path)
        if full.exists():
            full.unlink()
        return True
    except Exception as e:
        logger.error(f"Delete failed for {path}: {e}")
        return False
