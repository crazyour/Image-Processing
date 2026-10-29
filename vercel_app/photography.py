"""Folder permissions and compressed media metadata for the photography center."""
from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any, Iterable

import psycopg

from .auth import database_connection, database_url


class PhotographyError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _execute(connection: Any, sql: str, parameters: tuple[Any, ...] = ()):
    if database_url():
        sql = sql.replace("?", "%s")
    return connection.execute(sql, parameters)


def _ensure_schema(connection: Any) -> None:
    if database_url():
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photography_folders (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_by TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photography_folder_access (
                folder_id TEXT NOT NULL REFERENCES photography_folders(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                granted_by TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (folder_id, user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photography_media (
                id TEXT PRIMARY KEY,
                folder_id TEXT NOT NULL REFERENCES photography_folders(id) ON DELETE CASCADE,
                display_name TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                original_extension TEXT NOT NULL,
                pathname TEXT NOT NULL,
                url TEXT NOT NULL,
                media_type TEXT NOT NULL,
                content_type TEXT NOT NULL,
                original_width INTEGER NOT NULL,
                original_height INTEGER NOT NULL,
                stored_width INTEGER NOT NULL,
                stored_height INTEGER NOT NULL,
                original_size_bytes BIGINT NOT NULL,
                stored_size_bytes BIGINT NOT NULL,
                uploader_id TEXT NOT NULL,
                uploader_name TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL
            )
            """
        )
    else:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photography_folders (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photography_folder_access (
                folder_id TEXT NOT NULL REFERENCES photography_folders(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                granted_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (folder_id, user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photography_media (
                id TEXT PRIMARY KEY,
                folder_id TEXT NOT NULL REFERENCES photography_folders(id) ON DELETE CASCADE,
                display_name TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                original_extension TEXT NOT NULL,
                pathname TEXT NOT NULL,
                url TEXT NOT NULL,
                media_type TEXT NOT NULL,
                content_type TEXT NOT NULL,
                original_width INTEGER NOT NULL,
                original_height INTEGER NOT NULL,
                stored_width INTEGER NOT NULL,
                stored_height INTEGER NOT NULL,
                original_size_bytes INTEGER NOT NULL,
                stored_size_bytes INTEGER NOT NULL,
                uploader_id TEXT NOT NULL,
                uploader_name TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
    connection.commit()


def _clean_folder_name(name: str) -> str:
    cleaned = " ".join(name.strip().split())
    if not cleaned or len(cleaned) > 80:
        raise PhotographyError("INVALID_FOLDER_NAME", "文件夹名称须为 1–80 个字符", 422)
    if any(character in cleaned for character in "/\\\0"):
        raise PhotographyError("INVALID_FOLDER_NAME", "文件夹名称不能包含斜杠或空字符", 422)
    return cleaned


def _validate_member_ids(connection: Any, member_ids: Iterable[str]) -> list[str]:
    unique_ids = list(dict.fromkeys(str(value) for value in member_ids if value))
    if not unique_ids:
        return []
    placeholders = ",".join("?" for _ in unique_ids)
    rows = _execute(
        connection,
        f"SELECT id FROM auth_users WHERE id IN ({placeholders}) AND role = ? AND active = ?",
        tuple(unique_ids) + ("member", True),
    ).fetchall()
    valid = {row["id"] for row in rows}
    missing = [user_id for user_id in unique_ids if user_id not in valid]
    if missing:
        raise PhotographyError("INVALID_FOLDER_MEMBER", "所选成员不存在或已停用", 422)
    return unique_ids


def _replace_access(connection: Any, folder_id: str, member_ids: Iterable[str], actor_id: str) -> None:
    valid_ids = _validate_member_ids(connection, member_ids)
    _execute(connection, "DELETE FROM photography_folder_access WHERE folder_id = ?", (folder_id,))
    now = time.time()
    for user_id in valid_ids:
        _execute(
            connection,
            """INSERT INTO photography_folder_access
               (folder_id, user_id, granted_by, created_at) VALUES (?, ?, ?, ?)""",
            (folder_id, user_id, actor_id, now),
        )


def create_folder(name: str, member_ids: Iterable[str], actor_id: str) -> dict[str, Any]:
    folder_id = str(uuid.uuid4())
    now = time.time()
    with database_connection() as connection:
        _ensure_schema(connection)
        try:
            _execute(
                connection,
                """INSERT INTO photography_folders
                   (id, name, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?)""",
                (folder_id, _clean_folder_name(name), actor_id, now, now),
            )
            _replace_access(connection, folder_id, member_ids, actor_id)
            connection.commit()
        except (sqlite3.IntegrityError, psycopg.errors.UniqueViolation) as exc:
            connection.rollback()
            raise PhotographyError("FOLDER_NAME_EXISTS", "已有同名文件夹", 409) from exc
    return get_folder_for_admin(folder_id)


def update_folder(folder_id: str, name: str, member_ids: Iterable[str], actor_id: str) -> dict[str, Any]:
    with database_connection() as connection:
        _ensure_schema(connection)
        if not _execute(connection, "SELECT id FROM photography_folders WHERE id = ?", (folder_id,)).fetchone():
            raise PhotographyError("FOLDER_NOT_FOUND", "文件夹不存在", 404)
        try:
            _execute(
                connection,
                "UPDATE photography_folders SET name = ?, updated_at = ? WHERE id = ?",
                (_clean_folder_name(name), time.time(), folder_id),
            )
            _replace_access(connection, folder_id, member_ids, actor_id)
            connection.commit()
        except (sqlite3.IntegrityError, psycopg.errors.UniqueViolation) as exc:
            connection.rollback()
            raise PhotographyError("FOLDER_NAME_EXISTS", "已有同名文件夹", 409) from exc
    return get_folder_for_admin(folder_id)


def delete_folder(folder_id: str) -> None:
    with database_connection() as connection:
        _ensure_schema(connection)
        count = _execute(
            connection, "SELECT COUNT(*) AS count FROM photography_media WHERE folder_id = ?", (folder_id,),
        ).fetchone()
        if count and int(count["count"]) > 0:
            raise PhotographyError("FOLDER_NOT_EMPTY", "文件夹中已有文件，不能删除", 409)
        cursor = _execute(connection, "DELETE FROM photography_folders WHERE id = ?", (folder_id,))
        connection.commit()
        if cursor.rowcount != 1:
            raise PhotographyError("FOLDER_NOT_FOUND", "文件夹不存在", 404)


def _folder_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "item_count": int(row["item_count"] or 0),
        "stored_size_bytes": int(row["stored_size_bytes"] or 0),
        "original_size_bytes": int(row["original_size_bytes"] or 0),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
    }


def list_folders(user: dict[str, Any]) -> list[dict[str, Any]]:
    with database_connection() as connection:
        _ensure_schema(connection)
        base = """
            SELECT f.id, f.name, f.created_at, f.updated_at,
                   COUNT(DISTINCT m.id) AS item_count,
                   COALESCE(SUM(m.stored_size_bytes), 0) AS stored_size_bytes,
                   COALESCE(SUM(m.original_size_bytes), 0) AS original_size_bytes
              FROM photography_folders f
              LEFT JOIN photography_media m ON m.folder_id = f.id
        """
        parameters: tuple[Any, ...] = ()
        if user["role"] != "admin":
            base += " JOIN photography_folder_access a ON a.folder_id = f.id WHERE a.user_id = ?"
            parameters = (user["id"],)
        base += " GROUP BY f.id, f.name, f.created_at, f.updated_at ORDER BY f.updated_at DESC, f.name"
        rows = _execute(connection, base, parameters).fetchall()
    return [_folder_dict(row) for row in rows]


def get_folder_for_admin(folder_id: str) -> dict[str, Any]:
    with database_connection() as connection:
        _ensure_schema(connection)
        row = _execute(
            connection,
            """SELECT f.id, f.name, f.created_at, f.updated_at,
                      COUNT(DISTINCT m.id) AS item_count,
                      COALESCE(SUM(m.stored_size_bytes), 0) AS stored_size_bytes,
                      COALESCE(SUM(m.original_size_bytes), 0) AS original_size_bytes
                 FROM photography_folders f
                 LEFT JOIN photography_media m ON m.folder_id = f.id
                WHERE f.id = ?
                GROUP BY f.id, f.name, f.created_at, f.updated_at""",
            (folder_id,),
        ).fetchone()
        if not row:
            raise PhotographyError("FOLDER_NOT_FOUND", "文件夹不存在", 404)
        members = _execute(
            connection,
            """SELECT u.id, u.username, u.name
                 FROM photography_folder_access a
                 JOIN auth_users u ON u.id = a.user_id
                WHERE a.folder_id = ? ORDER BY u.name, u.username""",
            (folder_id,),
        ).fetchall()
    result = _folder_dict(row)
    result["members"] = [dict(member) for member in members]
    result["member_ids"] = [member["id"] for member in members]
    return result


def list_folders_for_admin() -> list[dict[str, Any]]:
    folders = list_folders({"id": "", "role": "admin"})
    detailed = {folder["id"]: get_folder_for_admin(folder["id"]) for folder in folders}
    return [detailed[folder["id"]] for folder in folders]


def can_access_folder(user: dict[str, Any], folder_id: str) -> bool:
    with database_connection() as connection:
        _ensure_schema(connection)
        if user["role"] == "admin":
            row = _execute(connection, "SELECT id FROM photography_folders WHERE id = ?", (folder_id,)).fetchone()
        else:
            row = _execute(
                connection,
                """SELECT f.id FROM photography_folders f
                   JOIN photography_folder_access a ON a.folder_id = f.id
                   WHERE f.id = ? AND a.user_id = ?""",
                (folder_id, user["id"]),
            ).fetchone()
    return bool(row)


def require_folder_access(user: dict[str, Any], folder_id: str) -> None:
    if not can_access_folder(user, folder_id):
        raise PhotographyError("FOLDER_FORBIDDEN", "你没有这个文件夹的访问权限", 403)


def save_media(record: dict[str, Any]) -> dict[str, Any]:
    with database_connection() as connection:
        _ensure_schema(connection)
        _execute(
            connection,
            """INSERT INTO photography_media
               (id, folder_id, display_name, original_filename, original_extension,
                pathname, url, media_type, content_type, original_width, original_height,
                stored_width, stored_height, original_size_bytes, stored_size_bytes,
                uploader_id, uploader_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["id"], record["folder_id"], record["display_name"],
                record["original_filename"], record["original_extension"], record["pathname"],
                record["url"], record["media_type"], record["content_type"],
                record["original_width"], record["original_height"], record["stored_width"],
                record["stored_height"], record["original_size_bytes"], record["stored_size_bytes"],
                record["uploader_id"], record["uploader_name"], record["created_at"],
            ),
        )
        _execute(
            connection, "UPDATE photography_folders SET updated_at = ? WHERE id = ?",
            (record["created_at"], record["folder_id"]),
        )
        connection.commit()
    return record


def _media_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("original_width", "original_height", "stored_width", "stored_height",
                "original_size_bytes", "stored_size_bytes"):
        result[key] = int(result[key])
    result["created_at"] = float(result["created_at"])
    return result


def list_media(user: dict[str, Any], folder_id: str) -> list[dict[str, Any]]:
    require_folder_access(user, folder_id)
    with database_connection() as connection:
        _ensure_schema(connection)
        rows = _execute(
            connection,
            "SELECT * FROM photography_media WHERE folder_id = ? ORDER BY created_at DESC",
            (folder_id,),
        ).fetchall()
    return [_media_dict(row) for row in rows]


def get_media(user: dict[str, Any], media_id: str) -> dict[str, Any]:
    with database_connection() as connection:
        _ensure_schema(connection)
        row = _execute(connection, "SELECT * FROM photography_media WHERE id = ?", (media_id,)).fetchone()
    if not row:
        raise PhotographyError("MEDIA_NOT_FOUND", "文件不存在", 404)
    result = _media_dict(row)
    require_folder_access(user, result["folder_id"])
    return result
