#!/usr/bin/env python3
"""Download the production material-search library into a local bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from vercel_app.auth import database_url  # noqa: E402
from vercel_app.similarity_store import _ensure_schema  # noqa: E402


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步线上图搜图素材到本地运行包")
    parser.add_argument("--base-url", required=True, help="生产站点地址，例如 https://example.com")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "var" / "material-library",
        help="本地素材包目录（默认：var/material-library）",
    )
    parser.add_argument("--username", default="", help="登录账号；默认读取 ADMIN_USERNAME")
    parser.add_argument("--password", default="", help="登录密码；默认读取 ADMIN_PASSWORD")
    parser.add_argument("--timeout", type=float, default=300.0, help="单个下载请求超时秒数")
    parser.add_argument("--workers", type=int, default=8, help="并行下载数（默认：8）")
    return parser.parse_args()


def safe_relative(reference: str) -> Path:
    prefix = "material-local://"
    value = reference[len(prefix):] if reference.startswith(prefix) else reference
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"素材引用路径无效：{reference!r}")
    return Path(*relative.parts)


def remote_rows(connection: Any, library_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    library = connection.execute(
        "SELECT * FROM material_libraries WHERE id = %s", (library_id,),
    ).fetchone()
    folders = connection.execute(
        "SELECT * FROM material_folders WHERE library_id = %s AND deleted_at IS NULL ORDER BY created_at",
        (library_id,),
    ).fetchall()
    assets = connection.execute(
        """
        SELECT a.* FROM material_assets a
        JOIN material_folders f ON f.id = a.folder_id
        WHERE f.library_id = %s AND f.deleted_at IS NULL
        ORDER BY a.folder_id, a.created_at
        """,
        (library_id,),
    ).fetchall()
    if not library:
        raise RuntimeError("远程数据库中没有公共素材库")
    return [dict(row) for row in folders], [dict(row) for row in assets], dict(library)


def download(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    destination: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> bool:
    if destination.is_file():
        actual_size = destination.stat().st_size
        size_ok = actual_size > 0 and (expected_size is None or actual_size == expected_size)
        hash_ok = expected_sha256 is None
        if size_ok and expected_sha256:
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            hash_ok = digest == expected_sha256
        if size_ok and hash_ok:
            return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        digest = hashlib.sha256()
        size = 0
        with temporary.open("wb") as output:
            for chunk in response.iter_bytes(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    if expected_size is not None and size != expected_size:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"下载大小不一致：{url}（{size} != {expected_size}）")
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"下载校验失败：{url}")
    temporary.replace(destination)
    return True


def insert_rows(database: Path, library: dict[str, Any], folders: Iterable[dict[str, Any]], assets: Iterable[dict[str, Any]]) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_schema(connection)
        connection.execute("DELETE FROM material_assets")
        connection.execute("DELETE FROM material_folders")
        connection.execute("DELETE FROM material_libraries")
        connection.execute(
            "INSERT INTO material_libraries (id, token_hash, created_at) VALUES (?, ?, ?)",
            (library["id"], library["token_hash"], library["created_at"]),
        )
        connection.executemany(
            """
            INSERT INTO material_folders
                (id, library_id, name, status, error_message, zip_url, deleted_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["id"], row["library_id"], row["name"], row["status"], row["error_message"],
                    row["zip_url"], row["deleted_at"], row["created_at"], row["updated_at"],
                )
                for row in folders
            ],
        )
        connection.executemany(
            """
            INSERT INTO material_assets
                (id, folder_id, kind, original_name, relative_path, blob_url, preview_url,
                 file_size, sha256, is_primary, vector_json, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["id"], row["folder_id"], row["kind"], row["original_name"], row["relative_path"],
                    row["blob_url"], row["preview_url"], row["file_size"], row["sha256"], row["is_primary"],
                    json.dumps(row["vector_json"]) if not isinstance(row["vector_json"], str) else row["vector_json"],
                    json.dumps(row["metadata_json"]) if not isinstance(row["metadata_json"], str) else row["metadata_json"],
                    row["created_at"],
                )
                for row in assets
            ],
        )
        connection.commit()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = arguments()
    username = args.username or os.getenv("ADMIN_USERNAME", "").strip()
    password = args.password or os.getenv("ADMIN_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("请通过参数或 .env 配置 ADMIN_USERNAME 和 ADMIN_PASSWORD")
    remote_database = database_url()
    if not remote_database:
        raise RuntimeError(".env 中没有可用的远程 PostgreSQL 连接")

    output = args.output.expanduser().resolve()
    os.environ["LOCAL_MATERIAL_LIBRARY_DIR"] = str(output)
    blob_root = output / "material-private"
    library_id = "public-material-library"
    library_token = os.getenv("MATERIAL_LIBRARY_TOKEN", "").strip() or "lingjie-public-material-library"
    headers = {"X-Material-Library": library_id, "X-Material-Token": library_token}

    with psycopg.connect(remote_database, row_factory=dict_row, connect_timeout=30) as connection:
        folders, assets, library = remote_rows(connection, library_id)

    base_url = args.base_url.rstrip("/")
    downloaded = 0
    skipped = 0
    with httpx.Client(base_url=base_url, timeout=args.timeout, follow_redirects=True) as client:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        login.raise_for_status()
        listing = client.get("/api/material-search/folders", headers=headers)
        listing.raise_for_status()
        online_ids = {folder["id"] for folder in listing.json().get("folders", [])}
        expected_ids = {folder["id"] for folder in folders}
        if online_ids != expected_ids:
            raise RuntimeError("线上下载接口与数据库中的有效素材文件夹不一致，请稍后重试")

        def sync_asset(asset: dict[str, Any]) -> tuple[int, int]:
            original = blob_root / safe_relative(asset["blob_url"])
            preview = blob_root / safe_relative(asset["preview_url"])
            original_changed = download(
                client,
                f"/api/material-search/assets/{asset['id']}/download",
                headers,
                original,
                expected_size=int(asset["file_size"]),
                expected_sha256=str(asset["sha256"]),
            )
            preview_changed = download(
                client,
                f"/api/material-search/assets/{asset['id']}/preview",
                headers,
                preview,
            )
            changed = int(original_changed) + int(preview_changed)
            return changed, 2 - changed

        workers = max(1, min(args.workers, 32))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(sync_asset, asset) for asset in assets]
            for index, future in enumerate(as_completed(futures), 1):
                changed, existing = future.result()
                downloaded += changed
                skipped += existing
                if index % 50 == 0 or index == len(assets):
                    print(f"素材文件：{index}/{len(assets)}（新下载 {downloaded}，已存在 {skipped}）", flush=True)

        for index, folder in enumerate(folders, 1):
            if not folder.get("zip_url"):
                continue
            destination = blob_root / safe_relative(folder["zip_url"])
            changed = download(
                client,
                f"/api/material-search/folders/{folder['id']}/download",
                headers,
                destination,
            )
            downloaded += int(changed)
            skipped += int(not changed)
            print(f"文件夹 ZIP：{index}/{len(folders)}", flush=True)

    insert_rows(output / "materials.sqlite3", library, folders, assets)
    print(f"同步完成：{len(folders)} 个文件夹，{len(assets)} 个素材；目录 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
