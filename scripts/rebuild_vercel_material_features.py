#!/usr/bin/env python3
"""Back up and rebuild production material-search image features via Vercel."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


def database_url() -> str:
    for name in ("NEON_DATABASE_URL", "POSTGRES_URL", "DATABASE_URL", "DATABASE_URL_POOLED"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise RuntimeError("未找到正式数据库连接")


def back_up_features(destination: Path) -> int:
    if destination.exists():
        raise RuntimeError(f"备份文件已存在，不会覆盖：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        with connection.cursor(name="material_feature_backup") as cursor:
            cursor.execute(
                """
                SELECT a.id, a.original_name, a.sha256, a.vector_json, a.metadata_json
                  FROM material_assets a
                  JOIN material_folders f ON f.id = a.folder_id
                 WHERE a.kind = 'image' AND f.deleted_at IS NULL
                 ORDER BY a.id
                """
            )
            with gzip.open(destination, "wt", encoding="utf-8") as output:
                for row in cursor:
                    output.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
                    output.write("\n")
                    count += 1
                    if count % 100 == 0:
                        print(f"BACKUP {count}", flush=True)
    print(f"BACKUP_COMPLETE count={count} path={destination} bytes={destination.stat().st_size}", flush=True)
    return count


class ProductionClient:
    def __init__(self, deployment: str) -> None:
        self.deployment = deployment
        self.cookie = self._login()
        library = self.request("/api/material-search/libraries", method="POST", library_headers=False)
        self.library_id = library["library_id"]
        self.library_token = library["access_token"]

    def _base_command(self, path: str) -> list[str]:
        return ["vercel", "curl", path, "--deployment", self.deployment, "--"]

    def _login(self) -> str:
        payload = json.dumps({
            "username": os.environ["ADMIN_USERNAME"],
            "password": os.environ["ADMIN_PASSWORD"],
        })
        command = self._base_command("/api/auth/login") + [
            "--silent", "--show-error", "--include", "--request", "POST",
            "--header", "Content-Type: application/json", "--data", payload,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError("正式站点登录请求失败")
        match = re.search(r"(?im)^set-cookie:\s*lingjie_session=([^;]+)", result.stdout)
        if not match:
            raise RuntimeError("正式站点未返回管理员会话")
        return match.group(1)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        library_headers: bool = True,
        attempts: int = 3,
    ) -> dict[str, Any]:
        command = self._base_command(path) + [
            "--silent", "--show-error", "--request", method,
            "--header", f"Cookie: lingjie_session={self.cookie}",
        ]
        if library_headers:
            command.extend([
                "--header", f"X-Material-Library: {self.library_id}",
                "--header", f"X-Material-Token: {self.library_token}",
            ])
        if data is not None:
            command.extend([
                "--header", "Content-Type: application/json",
                "--data", json.dumps(data, separators=(",", ":")),
            ])
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=330)
            except subprocess.TimeoutExpired:
                last_error = "请求超过 330 秒"
            else:
                if result.returncode == 0:
                    try:
                        payload = json.loads(result.stdout)
                    except json.JSONDecodeError:
                        last_error = "正式接口未返回 JSON"
                    else:
                        if isinstance(payload, dict) and not payload.get("code"):
                            return payload
                        last_error = str(payload.get("message") or payload.get("detail") or payload)
                else:
                    last_error = (result.stderr or "Vercel CLI 请求失败").strip().splitlines()[-1]
            if attempt < attempts:
                time.sleep(attempt * 2)
        raise RuntimeError(last_error)


def version_counts() -> dict[str, int]:
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT COALESCE(a.metadata_json::jsonb->>'feature_version', 'missing') AS version,
                   COUNT(*) AS count
              FROM material_assets a
              JOIN material_folders f ON f.id = a.folder_id
             WHERE a.kind = 'image' AND f.deleted_at IS NULL
             GROUP BY 1 ORDER BY 1
            """
        ).fetchall()
    return {str(row["version"]): int(row["count"]) for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", default="https://image-processing-tau.vercel.app")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--skip-current", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10, choices=range(1, 11))
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 9))
    parser.add_argument("--report-every", type=int, default=500)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.report_every < 1:
        parser.error("--report-every must be at least 1")
    load_dotenv(args.env_file, override=False)

    if args.backup:
        back_up_features(args.backup)

    client = ProductionClient(args.deployment)
    plan = client.request("/api/material-search/reindex/plan")
    items = list(plan["items"])
    if args.skip_current:
        items = [item for item in items if item.get("current_version") != plan["feature_version"]]
    if args.limit is not None:
        items = items[: max(0, args.limit)]
    print(json.dumps({
        "production_total": plan["total"],
        "selected": len(items),
        "target_version": plan["feature_version"],
        "before": version_counts(),
    }, ensure_ascii=False), flush=True)
    if args.plan_only:
        return 0

    started = time.monotonic()
    updated = 0
    failures: list[dict[str, str]] = []
    batches = [items[offset:offset + args.batch_size] for offset in range(0, len(items), args.batch_size)]

    def rebuild_batch(batch: list[dict[str, Any]]) -> tuple[int, list[dict[str, str]]]:
        try:
            result = client.request(
                "/api/material-search/reindex/batch",
                method="POST",
                data={"asset_ids": [item["asset_id"] for item in batch]},
            )
        except RuntimeError as exc:
            return 0, [{"asset_id": item["asset_id"], "error": str(exc)} for item in batch]
        return int(result["updated"]), list(result.get("failures") or [])

    completed = 0
    next_report = min(args.report_every, len(items)) if items else 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = {executor.submit(rebuild_batch, batch): batch for batch in batches}
        for future in as_completed(pending):
            batch = pending[future]
            batch_updated, batch_failures = future.result()
            updated += batch_updated
            failures.extend(batch_failures)
            completed += len(batch)
            if completed >= next_report or completed == len(items):
                elapsed = time.monotonic() - started
                print(
                    f"PROGRESS {completed}/{len(items)} updated={updated} failed={len(failures)} "
                    f"elapsed_seconds={elapsed:.1f}",
                    flush=True,
                )
                while next_report <= completed:
                    next_report += args.report_every

    print(json.dumps({
        "selected": len(items),
        "updated": updated,
        "failed": len(failures),
        "failures": failures[:50],
        "after": version_counts(),
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }, ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
