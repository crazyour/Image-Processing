"""Account authentication and feature authorization for the cloud application."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


FEATURES = {
    "text_to_image": "文生图",
    "animal_cutout": "照片转镂空剪影",
    "image_to_3d": "图生 3D",
    "similarity_search": "相似素材检索",
    "asset_library": "成果素材库",
}
ALWAYS_AVAILABLE_FEATURES = frozenset({"workspace"})
DEFAULT_FEATURES = tuple(FEATURES)
SESSION_COOKIE = "lingjie_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
_schema_lock = threading.Lock()


class AuthError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _configured(name: str) -> str:
    return os.getenv(name, "").strip()


def _local_database_path() -> Path:
    configured = _configured("LOCAL_DATA_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    elif _configured("VERCEL"):
        root = Path("/tmp/lingjie-image3d")
    else:
        root = Path(__file__).resolve().parent.parent / "var"
    return root / "image3d.sqlite3"


def _is_postgres() -> bool:
    return bool(_configured("DATABASE_URL"))


@contextmanager
def _connection() -> Iterator[Any]:
    if _is_postgres():
        connection = psycopg.connect(
            _configured("DATABASE_URL"), row_factory=dict_row, connect_timeout=10,
        )
    else:
        path = _local_database_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
    try:
        _ensure_schema(connection)
        yield connection
    finally:
        connection.close()


def database_connection() -> Any:
    """Shared database connection for account-scoped modules such as usage."""
    return _connection()


def _ensure_schema(connection: Any) -> None:
    with _schema_lock:
        if _is_postgres():
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    features JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at DOUBLE PRECISION NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                    expires_at DOUBLE PRECISION NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                )
                """
            )
        else:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    features TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
        connection.commit()


def _placeholder() -> str:
    return "%s" if _is_postgres() else "?"


def _execute(connection: Any, sql: str, parameters: tuple[Any, ...] = ()):
    if _is_postgres():
        sql = sql.replace("?", "%s")
    return connection.execute(sql, parameters)


def _normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise AuthError("INVALID_USERNAME", "账号须为 3–50 位字母、数字、点、下划线或短横线", 422)
    return normalized


def _validate_password(password: str) -> None:
    if not password or len(password) > 150:
        raise AuthError("INVALID_PASSWORD", "密码不能为空且最多 150 个字符", 422)


def _hash_password(password: str) -> str:
    _validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt),
            n=int(n), r=int(r), p=int(p),
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _features_json(features: list[str] | tuple[str, ...]) -> str:
    invalid = set(features) - set(FEATURES) - ALWAYS_AVAILABLE_FEATURES
    if invalid:
        raise AuthError("INVALID_FEATURE", f"未知功能权限：{', '.join(sorted(invalid))}", 422)
    assignable = [feature for feature in features if feature in FEATURES]
    return json.dumps(list(dict.fromkeys(assignable)), ensure_ascii=False)


def _user_dict(row: Any) -> dict[str, Any]:
    features = row["features"]
    if isinstance(features, str):
        features = json.loads(features)
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"],
        "role": row["role"],
        "active": bool(row["active"]),
        "features": [feature for feature in list(features or []) if feature in FEATURES],
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
    }


def ensure_initial_admin() -> bool:
    """Create the first administrator only from server-side environment variables."""
    with _connection() as connection:
        row = _execute(connection, "SELECT id FROM auth_users LIMIT 1").fetchone()
        if row:
            return True
        username = _configured("ADMIN_USERNAME")
        password = _configured("ADMIN_PASSWORD")
        name = _configured("ADMIN_NAME") or "系统管理员"
        if not username or not password:
            return False
        _insert_user(connection, username, name, password, "admin", list(DEFAULT_FEATURES))
        connection.commit()
        return True


def setup_required() -> bool:
    ensure_initial_admin()
    with _connection() as connection:
        return _execute(connection, "SELECT id FROM auth_users LIMIT 1").fetchone() is None


def _insert_user(connection: Any, username: str, name: str, password: str,
                 role: str, features: list[str]) -> dict[str, Any]:
    username = _normalize_username(username)
    name = name.strip()
    if not name or len(name) > 100:
        raise AuthError("INVALID_NAME", "显示名称须为 1–100 个字符", 422)
    if role not in {"admin", "member"}:
        raise AuthError("INVALID_ROLE", "账号角色无效", 422)
    now = time.time()
    user_id = str(uuid.uuid4())
    try:
        _execute(
            connection,
            """INSERT INTO auth_users
               (id, username, password_hash, name, role, active, features, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, _hash_password(password), name, role, True,
             _features_json(features), now, now),
        )
    except (sqlite3.IntegrityError, psycopg.errors.UniqueViolation) as exc:
        raise AuthError("USERNAME_EXISTS", "该登录账号已存在", 409) from exc
    row = _execute(connection, "SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
    return _user_dict(row)


def create_user(username: str, name: str, password: str, role: str = "member",
                features: list[str] | None = None) -> dict[str, Any]:
    with _connection() as connection:
        user = _insert_user(connection, username, name, password, role, features or [])
        connection.commit()
        return user


def authenticate(username: str, password: str) -> tuple[str, dict[str, Any]]:
    ensure_initial_admin()
    try:
        normalized = _normalize_username(username)
    except AuthError:
        normalized = username.strip().lower()
    with _connection() as connection:
        row = _execute(connection, "SELECT * FROM auth_users WHERE username = ?", (normalized,)).fetchone()
        encoded = row["password_hash"] if row else _hash_password("dummy-password-value")
        if not row or not bool(row["active"]) or not _verify_password(password, encoded):
            raise AuthError("LOGIN_FAILED", "账号或密码错误", 401)
        token = secrets.token_urlsafe(48)
        now = time.time()
        _execute(connection, "DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
        _execute(
            connection,
            "INSERT INTO auth_sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), row["id"], now + SESSION_TTL_SECONDS, now),
        )
        connection.commit()
        return token, _user_dict(row)


def current_user(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    ensure_initial_admin()
    with _connection() as connection:
        row = _execute(
            connection,
            """SELECT u.* FROM auth_sessions s
               JOIN auth_users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = ?""",
            (_token_hash(token), time.time(), True),
        ).fetchone()
        return _user_dict(row) if row else None


def revoke_session(token: str | None) -> None:
    if not token:
        return
    with _connection() as connection:
        _execute(connection, "DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))
        connection.commit()


def list_users() -> list[dict[str, Any]]:
    with _connection() as connection:
        rows = _execute(connection, "SELECT * FROM auth_users ORDER BY created_at, username").fetchall()
        return [_user_dict(row) for row in rows]


def update_user(user_id: str, changes: dict[str, Any], actor_id: str) -> dict[str, Any]:
    allowed = {"username", "name", "password", "role", "active", "features"}
    if set(changes) - allowed:
        raise AuthError("INVALID_UPDATE", "包含不支持的账号字段", 422)
    with _connection() as connection:
        row = _execute(connection, "SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise AuthError("USER_NOT_FOUND", "账号不存在", 404)
        values: dict[str, Any] = {}
        if "username" in changes:
            values["username"] = _normalize_username(str(changes["username"]))
        if "name" in changes:
            name = str(changes["name"]).strip()
            if not name or len(name) > 100:
                raise AuthError("INVALID_NAME", "显示名称须为 1–100 个字符", 422)
            values["name"] = name
        if changes.get("password"):
            values["password_hash"] = _hash_password(str(changes["password"]))
        if "role" in changes:
            if changes["role"] not in {"admin", "member"}:
                raise AuthError("INVALID_ROLE", "账号角色无效", 422)
            values["role"] = changes["role"]
        if "active" in changes:
            values["active"] = bool(changes["active"])
        if "features" in changes:
            values["features"] = _features_json(list(changes["features"]))

        removing_admin = row["role"] == "admin" and (
            values.get("role") == "member" or values.get("active") is False
        )
        if removing_admin:
            count = _execute(
                connection, "SELECT COUNT(*) AS count FROM auth_users WHERE role = ? AND active = ?",
                ("admin", True),
            ).fetchone()["count"]
            if int(count) <= 1:
                raise AuthError("LAST_ADMIN", "不能停用或降级最后一个管理员", 409)
        if user_id == actor_id and values.get("active") is False:
            raise AuthError("SELF_DISABLE", "不能停用当前登录账号", 409)

        if values:
            values["updated_at"] = time.time()
            assignments = ", ".join(f"{column} = ?" for column in values)
            parameters = tuple(values.values()) + (user_id,)
            try:
                _execute(connection, f"UPDATE auth_users SET {assignments} WHERE id = ?", parameters)
            except (sqlite3.IntegrityError, psycopg.errors.UniqueViolation) as exc:
                raise AuthError("USERNAME_EXISTS", "该登录账号已存在", 409) from exc
            if any(key in values for key in ("password_hash", "role", "active")):
                if user_id == actor_id and values.get("active", True) and values.get("role", row["role"]) == "admin":
                    token_rows = _execute(
                        connection,
                        "SELECT token_hash FROM auth_sessions WHERE user_id = ? ORDER BY created_at DESC",
                        (user_id,),
                    ).fetchall()
                    keep = token_rows[0]["token_hash"] if token_rows else ""
                    _execute(
                        connection,
                        "DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?",
                        (user_id, keep),
                    )
                else:
                    _execute(connection, "DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        connection.commit()
        updated = _execute(connection, "SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
        return _user_dict(updated)


def has_feature(user: dict[str, Any], feature: str) -> bool:
    if user["role"] == "admin" or feature in ALWAYS_AVAILABLE_FEATURES:
        return True
    return feature in user["features"]
