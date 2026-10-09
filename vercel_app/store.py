"""Durable PostgreSQL session state and Vercel Blob object storage."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
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


_schema_ready = False
_schema_lock = threading.Lock()


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise StoreError("尚未配置 DATABASE_URL")
    return value


def _connect():
    connection = psycopg.connect(_database_url(), row_factory=dict_row)
    _ensure_schema(connection)
    return connection


def _ensure_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
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
        _schema_ready = True


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(session_id: str, token: str, state: dict[str, Any]) -> int:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO image3d_sessions (id, token_hash, state) VALUES (%s, %s, %s::jsonb)",
            (session_id, token_hash(token), json.dumps(state, ensure_ascii=False)),
        )
        connection.commit()
    return 1


def load_session(session_id: str, token: str) -> tuple[dict[str, Any], int]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT token_hash, state, version FROM image3d_sessions WHERE id = %s",
            (session_id,),
        ).fetchone()
    if not row or not hmac.compare_digest(row["token_hash"], token_hash(token)):
        raise SessionNotFound("会话不存在或访问令牌无效")
    state = row["state"]
    if isinstance(state, str):
        state = json.loads(state)
    return state, int(row["version"])


def save_session(session_id: str, state: dict[str, Any], expected_version: int) -> int:
    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE image3d_sessions
               SET state = %s::jsonb, version = version + 1, updated_at = NOW()
             WHERE id = %s AND version = %s
            """,
            (json.dumps(state, ensure_ascii=False), session_id, expected_version),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise SessionConflict("会话已被另一个请求更新，请刷新后重试")
        connection.commit()
    return expected_version + 1


class PublicBlobStore:
    def __init__(self) -> None:
        if not os.getenv("BLOB_READ_WRITE_TOKEN", "").strip():
            raise StoreError("尚未配置 BLOB_READ_WRITE_TOKEN")
        self.client = BlobClient()

    def put(self, pathname: str, data: bytes, content_type: str) -> str:
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
