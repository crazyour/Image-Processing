"""Resolve the optional on-disk material-library bundle used by local runs."""
from __future__ import annotations

import os
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_MATERIAL_DATABASE = "materials.sqlite3"


def local_material_library_root() -> Path | None:
    """Return the local bundle root when explicitly configured or downloaded.

    A downloaded bundle is auto-detected only outside Vercel.  This keeps the
    normal cloud PostgreSQL + Private Blob behaviour unchanged while making a
    plain ``uvicorn app:app --reload`` use the local copy automatically.
    """
    configured = os.getenv("LOCAL_MATERIAL_LIBRARY_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.getenv("VERCEL", "").strip() or os.getenv("LOCAL_DATA_DIR", "").strip():
        return None
    bundled = (_PROJECT_ROOT / "var" / "material-library").resolve()
    if (bundled / LOCAL_MATERIAL_DATABASE).is_file():
        return bundled
    return None


def local_material_database_path() -> Path | None:
    root = local_material_library_root()
    return root / LOCAL_MATERIAL_DATABASE if root else None
