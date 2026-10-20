"""Persistence for the standalone material similarity search workspace."""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from typing import Any

from .store import _configured, _connect_postgres, _connect_sqlite, token_hash


_schema_lock = threading.Lock()
_schema_ready: set[str] = set()


def _postgres() -> bool:
    return bool(_configured("DATABASE_URL"))


def _connection():
    return _connect_postgres() if _postgres() else _connect_sqlite()


def _ensure_schema(connection) -> None:
    if _postgres():
        key = "postgres"
    else:
        database = connection.execute("PRAGMA database_list").fetchone()
        key = f"sqlite:{database['file'] if database else id(connection)}"
    if key in _schema_ready:
        return
    with _schema_lock:
        if key in _schema_ready:
            return
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS material_libraries (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS material_folders (
                id TEXT PRIMARY KEY,
                library_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                zip_url TEXT,
                deleted_at DOUBLE PRECISION,
                created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL,
                FOREIGN KEY(library_id) REFERENCES material_libraries(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS material_assets (
                id TEXT PRIMARY KEY,
                folder_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                original_name TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                blob_url TEXT NOT NULL,
                preview_url TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                vector_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                UNIQUE(folder_id, relative_path),
                FOREIGN KEY(folder_id) REFERENCES material_folders(id)
            )
            """
        )
        if _postgres():
            connection.execute("ALTER TABLE material_folders ADD COLUMN IF NOT EXISTS deleted_at DOUBLE PRECISION")
        else:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(material_folders)").fetchall()}
            if "deleted_at" not in columns:
                connection.execute("ALTER TABLE material_folders ADD COLUMN deleted_at DOUBLE PRECISION")
        connection.execute("CREATE INDEX IF NOT EXISTS material_folder_library_idx ON material_folders(library_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS material_folder_deleted_idx ON material_folders(library_id, deleted_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS material_asset_folder_idx ON material_assets(folder_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS material_asset_kind_idx ON material_assets(kind)")
        connection.commit()
        _schema_ready.add(key)


def _execute(connection, postgres_sql: str, sqlite_sql: str, parameters: tuple[Any, ...] = ()):
    return connection.execute(postgres_sql if _postgres() else sqlite_sql, parameters)


def create_library() -> dict[str, str]:
    library_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    with _connection() as connection:
        _ensure_schema(connection)
        _execute(
            connection,
            "INSERT INTO material_libraries (id, token_hash, created_at) VALUES (%s, %s, %s)",
            "INSERT INTO material_libraries (id, token_hash, created_at) VALUES (?, ?, ?)",
            (library_id, token_hash(token), time.time()),
        )
        connection.commit()
    return {"library_id": library_id, "access_token": token}


def require_library(library_id: str, token: str) -> None:
    with _connection() as connection:
        _ensure_schema(connection)
        row = _execute(
            connection,
            "SELECT token_hash FROM material_libraries WHERE id = %s",
            "SELECT token_hash FROM material_libraries WHERE id = ?",
            (library_id,),
        ).fetchone()
    if not row or not secrets.compare_digest(str(row["token_hash"]), token_hash(token)):
        raise PermissionError("素材库不存在或访问令牌无效")


def create_folder(library_id: str, name: str) -> dict[str, Any]:
    folder_id = uuid.uuid4().hex
    now = time.time()
    with _connection() as connection:
        _ensure_schema(connection)
        _execute(
            connection,
            "INSERT INTO material_folders (id, library_id, name, status, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
            "INSERT INTO material_folders (id, library_id, name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (folder_id, library_id, name, "UPLOADING", now, now),
        )
        connection.commit()
    return {"id": folder_id, "name": name, "status": "UPLOADING", "created_at": now, "assets": []}


def _asset_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "original_name": row["original_name"],
        "relative_path": row["relative_path"],
        "blob_url": f"/api/material-search/assets/{row['id']}/download",
        "preview_url": f"/api/material-search/assets/{row['id']}/preview",
        "_blob_ref": row["blob_url"],
        "_preview_ref": row["preview_url"],
        "file_size": int(row["file_size"]),
        "sha256": row["sha256"],
        "is_primary": bool(row["is_primary"]),
        "metadata": json.loads(row["metadata_json"] or "{}"),
    }


def get_asset(library_id: str, asset_id: str) -> dict[str, Any] | None:
    with _connection() as connection:
        _ensure_schema(connection)
        row = _execute(
            connection,
            """
            SELECT a.* FROM material_assets a
            JOIN material_folders f ON f.id = a.folder_id
            WHERE a.id = %s AND f.library_id = %s AND f.deleted_at IS NULL
            """,
            """
            SELECT a.* FROM material_assets a
            JOIN material_folders f ON f.id = a.folder_id
            WHERE a.id = ? AND f.library_id = ? AND f.deleted_at IS NULL
            """,
            (asset_id, library_id),
        ).fetchone()
    return _asset_payload(row) if row else None


def _folder_payload(folder: Any, assets: list[Any]) -> dict[str, Any]:
    asset_payloads = [_asset_payload(row) for row in assets]
    previews: dict[str, str] = {}
    for asset in asset_payloads:
        previews.setdefault(asset["kind"], asset["preview_url"])
    return {
        "id": folder["id"],
        "name": folder["name"],
        "status": folder["status"],
        "error_message": folder["error_message"],
        "zip_url": folder["zip_url"],
        "created_at": float(folder["created_at"]),
        "updated_at": float(folder["updated_at"]),
        "deleted_at": float(folder["deleted_at"]) if folder["deleted_at"] is not None else None,
        "previews": previews,
        "assets": asset_payloads,
    }


def get_folder(library_id: str, folder_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
    with _connection() as connection:
        _ensure_schema(connection)
        deleted_filter = "" if include_deleted else " AND deleted_at IS NULL"
        folder = connection.execute(
            ("SELECT * FROM material_folders WHERE id = %s AND library_id = %s" if _postgres()
             else "SELECT * FROM material_folders WHERE id = ? AND library_id = ?") + deleted_filter,
            (folder_id, library_id),
        ).fetchone()
        if not folder:
            return None
        assets = _execute(
            connection,
            "SELECT * FROM material_assets WHERE folder_id = %s ORDER BY kind, is_primary DESC, original_name",
            "SELECT * FROM material_assets WHERE folder_id = ? ORDER BY kind, is_primary DESC, original_name",
            (folder_id,),
        ).fetchall()
    return _folder_payload(folder, list(assets))


def list_folders(library_id: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        _ensure_schema(connection)
        folders = _execute(
            connection,
            "SELECT * FROM material_folders WHERE library_id = %s AND deleted_at IS NULL ORDER BY created_at DESC",
            "SELECT * FROM material_folders WHERE library_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
            (library_id,),
        ).fetchall()
        result = []
        for folder in folders:
            assets = _execute(
                connection,
                "SELECT * FROM material_assets WHERE folder_id = %s ORDER BY kind, is_primary DESC, original_name",
                "SELECT * FROM material_assets WHERE folder_id = ? ORDER BY kind, is_primary DESC, original_name",
                (folder["id"],),
            ).fetchall()
            result.append(_folder_payload(folder, list(assets)))
    return result


def list_deleted_folders(library_id: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        _ensure_schema(connection)
        folders = _execute(
            connection,
            "SELECT * FROM material_folders WHERE library_id = %s AND deleted_at IS NOT NULL ORDER BY deleted_at DESC",
            "SELECT * FROM material_folders WHERE library_id = ? AND deleted_at IS NOT NULL ORDER BY deleted_at DESC",
            (library_id,),
        ).fetchall()
        result = []
        for folder in folders:
            assets = _execute(
                connection,
                "SELECT * FROM material_assets WHERE folder_id = %s ORDER BY kind, is_primary DESC, original_name",
                "SELECT * FROM material_assets WHERE folder_id = ? ORDER BY kind, is_primary DESC, original_name",
                (folder["id"],),
            ).fetchall()
            result.append(_folder_payload(folder, list(assets)))
    return result


def move_folder_to_trash(library_id: str, folder_id: str) -> bool:
    now = time.time()
    with _connection() as connection:
        _ensure_schema(connection)
        cursor = _execute(
            connection,
            "UPDATE material_folders SET deleted_at = %s, updated_at = %s WHERE id = %s AND library_id = %s AND deleted_at IS NULL",
            "UPDATE material_folders SET deleted_at = ?, updated_at = ? WHERE id = ? AND library_id = ? AND deleted_at IS NULL",
            (now, now, folder_id, library_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def restore_folder(library_id: str, folder_id: str) -> bool:
    with _connection() as connection:
        _ensure_schema(connection)
        cursor = _execute(
            connection,
            "UPDATE material_folders SET deleted_at = NULL, updated_at = %s WHERE id = %s AND library_id = %s AND deleted_at IS NOT NULL",
            "UPDATE material_folders SET deleted_at = NULL, updated_at = ? WHERE id = ? AND library_id = ? AND deleted_at IS NOT NULL",
            (time.time(), folder_id, library_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def expired_deleted_folders(library_id: str, cutoff: float) -> list[dict[str, Any]]:
    return [folder for folder in list_deleted_folders(library_id) if folder["deleted_at"] <= cutoff]


def permanently_delete_folder(library_id: str, folder_id: str) -> bool:
    with _connection() as connection:
        _ensure_schema(connection)
        folder = _execute(
            connection,
            "SELECT id FROM material_folders WHERE id = %s AND library_id = %s AND deleted_at IS NOT NULL",
            "SELECT id FROM material_folders WHERE id = ? AND library_id = ? AND deleted_at IS NOT NULL",
            (folder_id, library_id),
        ).fetchone()
        if not folder:
            return False
        _execute(
            connection,
            "DELETE FROM material_assets WHERE folder_id = %s",
            "DELETE FROM material_assets WHERE folder_id = ?",
            (folder_id,),
        )
        _execute(
            connection,
            "DELETE FROM material_folders WHERE id = %s AND library_id = %s",
            "DELETE FROM material_folders WHERE id = ? AND library_id = ?",
            (folder_id, library_id),
        )
        connection.commit()
    return True


def save_asset(
    folder_id: str,
    *,
    kind: str,
    original_name: str,
    relative_path: str,
    blob_url: str,
    preview_url: str,
    file_size: int,
    sha256: str,
    vector: list[float],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    asset_id = uuid.uuid4().hex
    now = time.time()
    vector_json = json.dumps(vector, separators=(",", ":"))
    metadata_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    with _connection() as connection:
        _ensure_schema(connection)
        primary_row = _execute(
            connection,
            "SELECT id FROM material_assets WHERE folder_id = %s AND kind = %s AND is_primary = 1 LIMIT 1",
            "SELECT id FROM material_assets WHERE folder_id = ? AND kind = ? AND is_primary = 1 LIMIT 1",
            (folder_id, kind),
        ).fetchone()
        is_primary = 0 if primary_row else 1
        if _postgres():
            connection.execute(
                """
                INSERT INTO material_assets
                    (id, folder_id, kind, original_name, relative_path, blob_url, preview_url,
                     file_size, sha256, is_primary, vector_json, metadata_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(folder_id, relative_path) DO UPDATE SET
                    kind = EXCLUDED.kind, original_name = EXCLUDED.original_name,
                    blob_url = EXCLUDED.blob_url, preview_url = EXCLUDED.preview_url,
                    file_size = EXCLUDED.file_size, sha256 = EXCLUDED.sha256,
                    vector_json = EXCLUDED.vector_json, metadata_json = EXCLUDED.metadata_json
                """,
                (asset_id, folder_id, kind, original_name, relative_path, blob_url, preview_url,
                 file_size, sha256, is_primary, vector_json, metadata_json, now),
            )
        else:
            connection.execute(
                """
                INSERT INTO material_assets
                    (id, folder_id, kind, original_name, relative_path, blob_url, preview_url,
                     file_size, sha256, is_primary, vector_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(folder_id, relative_path) DO UPDATE SET
                    kind = excluded.kind, original_name = excluded.original_name,
                    blob_url = excluded.blob_url, preview_url = excluded.preview_url,
                    file_size = excluded.file_size, sha256 = excluded.sha256,
                    vector_json = excluded.vector_json, metadata_json = excluded.metadata_json
                """,
                (asset_id, folder_id, kind, original_name, relative_path, blob_url, preview_url,
                 file_size, sha256, is_primary, vector_json, metadata_json, now),
            )
        _execute(
            connection,
            "UPDATE material_folders SET status = %s, error_message = NULL, updated_at = %s WHERE id = %s",
            "UPDATE material_folders SET status = ?, error_message = NULL, updated_at = ? WHERE id = ?",
            ("PROCESSING", now, folder_id),
        )
        connection.commit()
        row = _execute(
            connection,
            "SELECT * FROM material_assets WHERE folder_id = %s AND relative_path = %s",
            "SELECT * FROM material_assets WHERE folder_id = ? AND relative_path = ?",
            (folder_id, relative_path),
        ).fetchone()
    return _asset_payload(row)


def set_folder_result(folder_id: str, *, status: str, zip_url: str | None = None, error: str | None = None) -> None:
    with _connection() as connection:
        _ensure_schema(connection)
        _execute(
            connection,
            "UPDATE material_folders SET status = %s, zip_url = %s, error_message = %s, updated_at = %s WHERE id = %s",
            "UPDATE material_folders SET status = ?, zip_url = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, zip_url, error, time.time(), folder_id),
        )
        connection.commit()


def searchable_assets(library_id: str, kind: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        _ensure_schema(connection)
        rows = _execute(
            connection,
            """
            SELECT a.*, f.name AS folder_name
            FROM material_assets a
            JOIN material_folders f ON f.id = a.folder_id
            WHERE f.library_id = %s AND f.status = 'READY' AND f.deleted_at IS NULL AND a.kind = %s
            """,
            """
            SELECT a.*, f.name AS folder_name
            FROM material_assets a
            JOIN material_folders f ON f.id = a.folder_id
            WHERE f.library_id = ? AND f.status = 'READY' AND f.deleted_at IS NULL AND a.kind = ?
            """,
            (library_id, kind),
        ).fetchall()
    return [
        {
            "folder_id": row["folder_id"],
            "folder_name": row["folder_name"],
            "asset_id": row["id"],
            "original_name": row["original_name"],
            "preview_url": row["preview_url"],
            "vector": json.loads(row["vector_json"]),
        }
        for row in rows
    ]
