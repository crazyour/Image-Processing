"""Durable session state and object storage with local development fallbacks."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

import psycopg
from psycopg.rows import dict_row
from vercel.blob import BlobClient


class StoreError(RuntimeError):
    pass


class SessionNotFound(StoreError):
    pass


class SessionConflict(StoreError):
    pass


_schema_ready: set[str] = set()
_schema_lock = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_BLOB_URL_PREFIX = "/local-blobs/"


class _SQLiteConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3.Connection, then release the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _configured(name: str) -> str:
    return os.getenv(name, "").strip()


def _local_data_root() -> Path:
    configured = _configured("LOCAL_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if _configured("VERCEL"):
        return Path("/tmp/lingjie-image3d")
    return _PROJECT_ROOT / "var"


def local_blob_root() -> Path:
    return _local_data_root() / "blobs"


def local_blob_path(url_or_path: str) -> Path | None:
    """Resolve a local blob URL without allowing traversal outside the blob root."""
    if not url_or_path.startswith(LOCAL_BLOB_URL_PREFIX):
        return None
    relative = PurePosixPath(url_or_path[len(LOCAL_BLOB_URL_PREFIX):])
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        return None
    root = local_blob_root().resolve()
    candidate = root.joinpath(*relative.parts).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate


def storage_status() -> dict[str, str]:
    return {
        "database": "postgresql" if _configured("DATABASE_URL") else "local-sqlite",
        "blob": "vercel-blob" if _configured("BLOB_READ_WRITE_TOKEN") else "local-filesystem",
    }


def local_library_available() -> bool:
    """Return whether this process owns a durable, single-user local library."""
    return not _configured("DATABASE_URL") and not _configured("VERCEL")


def _connect_postgres():
    connection = psycopg.connect(
        _configured("DATABASE_URL"), row_factory=dict_row, connect_timeout=10,
    )
    _ensure_postgres_schema(connection)
    return connection


def _ensure_postgres_schema(connection) -> None:
    key = "postgresql"
    if key in _schema_ready:
        return
    with _schema_lock:
        if key in _schema_ready:
            return
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS image3d_sessions (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL,
                state JSONB NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        connection.commit()
        _schema_ready.add(key)


def _connect_sqlite() -> sqlite3.Connection:
    database_path = _local_data_root() / "image3d.sqlite3"
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=30, factory=_SQLiteConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        _ensure_sqlite_schema(connection, database_path)
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise StoreError(f"本地数据库打开失败：{exc}") from exc


def _ensure_sqlite_schema(connection: sqlite3.Connection, database_path: Path) -> None:
    key = f"sqlite:{database_path}"
    if key in _schema_ready:
        return
    with _schema_lock:
        if key in _schema_ready:
            return
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS image3d_sessions (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS local_preferences (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                prompt TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()
        _schema_ready.add(key)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(session_id: str, token: str, state: dict[str, Any]) -> int:
    serialized = json.dumps(state, ensure_ascii=False)
    if _configured("DATABASE_URL"):
        with _connect_postgres() as connection:
            connection.execute(
                "INSERT INTO image3d_sessions (id, token_hash, state) VALUES (%s, %s, %s::jsonb)",
                (session_id, token_hash(token), serialized),
            )
            connection.commit()
    else:
        with _connect_sqlite() as connection:
            connection.execute(
                "INSERT INTO image3d_sessions (id, token_hash, state) VALUES (?, ?, ?)",
                (session_id, token_hash(token), serialized),
            )
            connection.commit()
    return 1


def load_session(session_id: str, token: str) -> tuple[dict[str, Any], int]:
    if _configured("DATABASE_URL"):
        with _connect_postgres() as connection:
            row = connection.execute(
                "SELECT token_hash, state, version FROM image3d_sessions WHERE id = %s",
                (session_id,),
            ).fetchone()
    else:
        with _connect_sqlite() as connection:
            row = connection.execute(
                "SELECT token_hash, state, version FROM image3d_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
    if not row or not hmac.compare_digest(row["token_hash"], token_hash(token)):
        raise SessionNotFound("会话不存在或访问令牌无效")
    state = row["state"]
    if isinstance(state, str):
        state = json.loads(state)
    return state, int(row["version"])


def save_session(session_id: str, state: dict[str, Any], expected_version: int) -> int:
    serialized = json.dumps(state, ensure_ascii=False)
    if _configured("DATABASE_URL"):
        with _connect_postgres() as connection:
            cursor = connection.execute(
                """
                UPDATE image3d_sessions
                   SET state = %s::jsonb, version = version + 1, updated_at = NOW()
                 WHERE id = %s AND version = %s
                """,
                (serialized, session_id, expected_version),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise SessionConflict("会话已被另一个请求更新，请刷新后重试")
            connection.commit()
    else:
        with _connect_sqlite() as connection:
            cursor = connection.execute(
                """
                UPDATE image3d_sessions
                   SET state = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
                 WHERE id = ? AND version = ?
                """,
                (serialized, session_id, expected_version),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise SessionConflict("会话已被另一个请求更新，请刷新后重试")
            connection.commit()
    return expected_version + 1


def list_local_sessions(limit: int = 200) -> list[dict[str, Any]]:
    if not local_library_available():
        raise StoreError("本地作品库仅在本机存储模式下可用")
    with _connect_sqlite() as connection:
        rows = connection.execute(
            """
            SELECT id, state, version, created_at, updated_at
              FROM image3d_sessions
             ORDER BY updated_at DESC
             LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    result = []
    for row in rows:
        try:
            state = json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
        except (TypeError, json.JSONDecodeError):
            continue
        result.append({
            "id": row["id"],
            "state": state,
            "version": int(row["version"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return result


def rotate_local_session_token(session_id: str, token: str) -> dict[str, Any]:
    """Issue a fresh task token when the local owner reopens a library item."""
    if not local_library_available():
        raise StoreError("本地作品库仅在本机存储模式下可用")
    with _connect_sqlite() as connection:
        row = connection.execute(
            "SELECT state FROM image3d_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            raise SessionNotFound("作品记录不存在")
        connection.execute(
            "UPDATE image3d_sessions SET token_hash = ? WHERE id = ?",
            (token_hash(token), session_id),
        )
        connection.commit()
    state = row["state"]
    return json.loads(state) if isinstance(state, str) else state


def update_local_session_library(session_id: str, values: dict[str, Any]) -> dict[str, Any]:
    if not local_library_available():
        raise StoreError("本地作品库仅在本机存储模式下可用")
    with _connect_sqlite() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT state, version FROM image3d_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            connection.rollback()
            raise SessionNotFound("作品记录不存在")
        state = json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
        library = state.setdefault("library", {})
        library.update(values)
        state["updated_at"] = int(time.time())
        serialized = json.dumps(state, ensure_ascii=False)
        connection.execute(
            """
            UPDATE image3d_sessions
               SET state = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (serialized, session_id),
        )
        connection.commit()
    return state


def load_local_preferences() -> dict[str, Any]:
    if not local_library_available():
        return {"enabled": False, "prompt": "", "updated_at": None}
    with _connect_sqlite() as connection:
        row = connection.execute(
            "SELECT enabled, prompt, updated_at FROM local_preferences WHERE id = 1"
        ).fetchone()
    if not row:
        return {"enabled": False, "prompt": "", "updated_at": None}
    return {
        "enabled": bool(row["enabled"]),
        "prompt": row["prompt"] or "",
        "updated_at": row["updated_at"],
    }


def save_local_preferences(enabled: bool, prompt: str) -> dict[str, Any]:
    if not local_library_available():
        raise StoreError("个人偏好仅在本机存储模式下可用")
    with _connect_sqlite() as connection:
        connection.execute(
            """
            INSERT INTO local_preferences (id, enabled, prompt, updated_at)
            VALUES (1, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled,
                prompt = excluded.prompt,
                updated_at = CURRENT_TIMESTAMP
            """,
            (1 if enabled else 0, prompt),
        )
        connection.commit()
    return load_local_preferences()


class PublicBlobStore:
    """Use Vercel Blob when configured, otherwise persist files under ``var/blobs``."""

    def __init__(self) -> None:
        self.local = not bool(_configured("BLOB_READ_WRITE_TOKEN"))
        self.client = None if self.local else BlobClient()

    def put(self, pathname: str, data: bytes, content_type: str) -> str:
        if self.local:
            relative = PurePosixPath(pathname)
            if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                raise StoreError("本地文件路径无效")
            destination = local_blob_root().joinpath(*relative.parts)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            except OSError as exc:
                raise StoreError(f"本地文件保存失败：{exc}") from exc
            return f"{LOCAL_BLOB_URL_PREFIX}{relative.as_posix()}"

        try:
            blob = self.client.put(
                pathname,
                data,
                access="public",
                content_type=content_type,
                add_random_suffix=False,
            )
            return blob.url
        except Exception as exc:
            raise StoreError("Vercel Blob 保存失败") from exc
