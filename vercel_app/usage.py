"""Usage ledger for provider API consumption, aggregated per account and feature."""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from vercel_app.auth import database_connection


FEATURE_LABELS = {
    "text_to_image": "文生图",
    "animal_cutout": "照片转镂空剪影",
    "image_to_3d": "图生 3D",
}

COST_KEYS = (
    "cost", "cost_usd", "total_cost", "usage_cost", "price", "total_price",
    "usd", "usd_cost", "amount", "billed", "billing",
)
INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens", "input")
OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens", "output")
TOTAL_TOKEN_KEYS = ("total_tokens", "total")

SOURCE_PROVIDER = "接口返回"
SOURCE_PRESET = "预设价格"
SOURCE_UNPRICED = "未计价"


def _configured_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("$", "").replace("USD", "").replace("usd", "").strip()
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _first_number(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in payload:
            continue
        number = _number(payload[key])
        if number is not None:
            return number
    return None


def find_cost(payload: Any) -> tuple[float | None, str | None]:
    """Read the price the provider reported and the field it came from."""
    if not isinstance(payload, dict):
        return None, None
    scopes: list[tuple[str, dict[str, Any]]] = []
    nested = payload.get("usage")
    if isinstance(nested, dict):
        scopes.append(("usage", nested))
    scopes.append(("", payload))
    for prefix, scope in scopes:
        for key in COST_KEYS:
            if key not in scope:
                continue
            value = scope[key]
            number = _number(value)
            if number is not None:
                return number, f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                for inner in ("total", "amount", "value", "usd", "cost"):
                    number = _number(value.get(inner))
                    if number is not None:
                        return number, f"{prefix}.{key}.{inner}" if prefix else f"{key}.{inner}"
    return None, None


def extract_cost(payload: Any) -> float | None:
    """Read the price the provider reported, if the response carries one."""
    return find_cost(payload)[0]


def _preset_price(model: str | None, feature: str) -> tuple[float, str]:
    """Fallback price used when the provider response does not carry a price."""
    name = (model or "").lower()
    if name.startswith("hy-"):
        return _configured_float("USAGE_PRICE_3D_PER_TASK", 0.5), SOURCE_PRESET
    if "image" in name:
        return _configured_float("USAGE_PRICE_IMAGE_PER_IMAGE", 0.14), SOURCE_PRESET
    input_price = _configured_float("USAGE_PRICE_VISION_INPUT_PER_MILLION", 0.0)
    output_price = _configured_float("USAGE_PRICE_VISION_OUTPUT_PER_MILLION", 0.0)
    if not input_price and not output_price:
        return 0.0, SOURCE_UNPRICED
    return 0.0, SOURCE_PRESET


def normalize_usage(payload: Any, model: str | None = None,
                    feature: str = "") -> dict[str, Any]:
    """Turn a provider response or usage payload into tokens and US dollars."""
    payload = payload if isinstance(payload, dict) else {}
    nested = payload.get("usage")
    nested = nested if isinstance(nested, dict) else {}
    scopes = (nested, payload)
    input_tokens = 0.0
    output_tokens = 0.0
    total_tokens = 0.0
    for scope in scopes:
        input_tokens = _first_number(scope, INPUT_TOKEN_KEYS) or input_tokens
        output_tokens = _first_number(scope, OUTPUT_TOKEN_KEYS) or output_tokens
        total_tokens = _first_number(scope, TOTAL_TOKEN_KEYS) or total_tokens
    if not total_tokens:
        total_tokens = input_tokens + output_tokens

    cost, field = find_cost(payload)
    source = SOURCE_PROVIDER if cost is not None else ""
    if cost is None:
        cost, source = _preset_price(model, feature)
        field = None
    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "total_tokens": int(total_tokens),
        "cost_usd": round(float(cost), 6),
        "cost_source": source,
        "cost_field": field,
    }


def _ensure_usage_schema(connection: Any) -> None:
    is_postgres = str(connection.__class__.__module__).startswith("psycopg")
    if is_postgres:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_usage_events (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                username TEXT,
                user_name TEXT,
                feature TEXT NOT NULL,
                action TEXT,
                model TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                cost_source TEXT,
                cost_field TEXT,
                raw TEXT,
                created_at DOUBLE PRECISION NOT NULL
            )
            """
        )
    else:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_usage_events (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                username TEXT,
                user_name TEXT,
                feature TEXT NOT NULL,
                action TEXT,
                model TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                cost_source TEXT,
                cost_field TEXT,
                raw TEXT,
                created_at REAL NOT NULL
            )
            """
        )
    _add_missing_columns(connection, is_postgres)
    connection.commit()


def _add_missing_columns(connection: Any, is_postgres: bool) -> None:
    """Add columns introduced after the table was first created."""
    wanted = {"cost_field": "TEXT", "raw": "TEXT"}
    if is_postgres:
        rows = connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'auth_usage_events'"
        ).fetchall()
        existing = {str(row["column_name"]) for row in rows}
    else:
        rows = connection.execute("PRAGMA table_info(auth_usage_events)").fetchall()
        existing = {str(row[1]) for row in rows}
    for column, column_type in wanted.items():
        if column in existing:
            continue
        connection.execute(
            f"ALTER TABLE auth_usage_events ADD COLUMN {column} {column_type}"
        )


def _raw_payload(payload: Any) -> str | None:
    """Keep the provider's own usage block so the price can be re-checked later."""
    if not isinstance(payload, dict) or not payload:
        return None
    try:
        return json.dumps(payload, ensure_ascii=False)[:2000]
    except (TypeError, ValueError):
        return None


def record_usage(user: dict[str, Any] | None, feature: str, model: str | None,
                 payload: Any, action: str = "") -> dict[str, Any]:
    """Store one provider call. Never breaks the request when storage fails."""
    normalized = normalize_usage(payload, model, feature)
    event = {
        "id": uuid.uuid4().hex,
        "user_id": (user or {}).get("id"),
        "username": (user or {}).get("username"),
        "user_name": (user or {}).get("name"),
        "feature": feature,
        "action": action,
        "model": model,
        "input_tokens": normalized["input_tokens"],
        "output_tokens": normalized["output_tokens"],
        "total_tokens": normalized["total_tokens"],
        "cost_usd": normalized["cost_usd"],
        "cost_source": normalized["cost_source"],
        "cost_field": normalized["cost_field"],
        "raw": _raw_payload(payload),
        "created_at": time.time(),
    }
    try:
        with database_connection() as connection:
            _ensure_usage_schema(connection)
            placeholder = "%s" if str(connection.__class__.__module__).startswith("psycopg") else "?"
            columns = (
                "id", "user_id", "username", "user_name", "feature", "action", "model",
                "input_tokens", "output_tokens", "total_tokens", "cost_usd", "cost_source",
                "cost_field", "raw", "created_at",
            )
            connection.execute(
                f"""INSERT INTO auth_usage_events
                    ({', '.join(columns)})
                    VALUES ({', '.join([placeholder] * len(columns))})""",
                tuple(event[column] for column in columns),
            )
            connection.commit()
    except Exception:
        return event
    return event


def usage_report(accounts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aggregate recorded usage per account and per feature."""
    try:
        with database_connection() as connection:
            _ensure_usage_schema(connection)
            rows = connection.execute(
                """SELECT user_id, username, user_name, feature, model, cost_source, cost_field,
                          COUNT(*) AS calls,
                          SUM(input_tokens) AS input_tokens,
                          SUM(output_tokens) AS output_tokens,
                          SUM(total_tokens) AS total_tokens,
                          SUM(cost_usd) AS cost_usd
                   FROM auth_usage_events
                   GROUP BY user_id, username, user_name, feature, model, cost_source, cost_field"""
            ).fetchall()
    except Exception:
        rows = []

    identities: dict[str, dict[str, Any]] = {}
    for account in accounts or []:
        identities[str(account["id"])] = account

    accounts_by_id: dict[str, dict[str, Any]] = {}
    feature_totals: dict[str, dict[str, Any]] = {}
    grand_total = 0.0
    grand_calls = 0
    for row in rows:
        data = dict(row)
        user_id = str(data.get("user_id") or "")
        identity = identities.get(user_id, {})
        name = identity.get("name") or data.get("user_name") or data.get("username") or "未知账号"
        username = identity.get("username") or data.get("username") or ""
        account = accounts_by_id.setdefault(user_id or username, {
            "user_id": user_id,
            "name": name,
            "username": username,
            "total_usd": 0.0,
            "total_calls": 0,
            "total_tokens": 0,
            "features": {},
            "sources": [],
        })
        feature = data["feature"]
        calls = int(data.get("calls") or 0)
        cost = float(data.get("cost_usd") or 0.0)
        tokens = int(data.get("total_tokens") or 0)
        source = data.get("cost_source") or SOURCE_UNPRICED
        field = data.get("cost_field")
        label = f"{source}（{field}）" if field else source
        bucket = account["features"].setdefault(feature, {
            "feature": feature,
            "label": FEATURE_LABELS.get(feature, feature),
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "models": [],
            "sources": [],
        })
        bucket["calls"] += calls
        bucket["input_tokens"] += int(data.get("input_tokens") or 0)
        bucket["output_tokens"] += int(data.get("output_tokens") or 0)
        bucket["total_tokens"] += tokens
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)
        bucket["models"].append({
            "model": data.get("model") or "-",
            "calls": calls,
            "total_tokens": tokens,
            "cost_usd": round(cost, 6),
        })
        priced = next(
            (item for item in bucket["sources"] if item["source"] == label), None,
        )
        if priced is None:
            bucket["sources"].append({
                "source": label, "calls": calls, "cost_usd": round(cost, 6),
            })
        else:
            priced["calls"] += calls
            priced["cost_usd"] = round(priced["cost_usd"] + cost, 6)
        account["total_usd"] = round(account["total_usd"] + cost, 6)
        account["total_calls"] += calls
        account["total_tokens"] += tokens
        account_source = next(
            (item for item in account["sources"] if item["source"] == label), None,
        )
        if account_source is None:
            account["sources"].append({
                "source": label, "calls": calls, "cost_usd": round(cost, 6),
            })
        else:
            account_source["calls"] += calls
            account_source["cost_usd"] = round(account_source["cost_usd"] + cost, 6)
        grand_total = round(grand_total + cost, 6)
        grand_calls += calls

        total = feature_totals.setdefault(feature, {
            "feature": feature,
            "label": FEATURE_LABELS.get(feature, feature),
            "calls": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        })
        total["calls"] += calls
        total["total_tokens"] += tokens
        total["cost_usd"] = round(total["cost_usd"] + cost, 6)

    account_rows = []
    for account in accounts_by_id.values():
        account["features"] = sorted(
            account["features"].values(), key=lambda item: item["cost_usd"], reverse=True,
        )
        account_rows.append(account)
    account_rows.sort(key=lambda item: item["total_usd"], reverse=True)

    return {
        "accounts": account_rows,
        "feature_totals": sorted(
            feature_totals.values(), key=lambda item: item["cost_usd"], reverse=True,
        ),
        "grand_total_usd": grand_total,
        "grand_total_calls": grand_calls,
    }


def _probe() -> int:
    """Check which price field would be picked from a provider response.

    Usage: echo '{"usage":{"cost":0.14}}' | python -m vercel_app.usage
    """
    import sys

    raw = sys.stdin.read().strip()
    if not raw:
        print("请把接口返回的 JSON 粘进来，例如：")
        print("""echo '{"usage":{"total_tokens":1200,"cost":0.14}}' | python -m vercel_app.usage""")
        return 1
    try:
        payload = json.loads(raw)
    except ValueError as error:
        print(f"JSON 解析失败：{error}")
        return 1
    cost, field = find_cost(payload)
    normalized = normalize_usage(payload)
    print(f"抓到费用：{cost}  字段：{field or '未找到'}")
    print(f"token：输入 {normalized['input_tokens']} / 输出 {normalized['output_tokens']}"
          f" / 合计 {normalized['total_tokens']}")
    print(f"计价方式：{normalized['cost_source']}")
    if cost is None:
        print("提示：这个响应里没有识别到费用字段，会退回预设单价。")
        print("把完整的接口返回贴给开发，可以据此补上对应的字段名。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_probe())
