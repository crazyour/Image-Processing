"""Usage ledger for provider API consumption, aggregated per account and feature."""
from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vercel_app.auth import database_connection
from vercel_app.pricing import (
    estimated_call_cost,
    input_token_details,
    pricing_signature,
    token_cost,
)
from vercel_app.settings import optional_float


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
SOURCE_TOKEN_PRICE = "Token 单价估算"
SOURCE_UNPRICED = "未计价"

# Tracks the price configuration and row count the stored rows were last repriced with.
_backfill_state: dict[str, Any] = {"signature": None, "count": None}

logger = logging.getLogger(__name__)

PERIODS = ("day", "week", "month")
LIMIT_COLUMNS = {
    "day": "daily_limit_usd",
    "week": "weekly_limit_usd",
    "month": "monthly_limit_usd",
}
RESET_COLUMNS = {
    "day": "daily_reset_at",
    "week": "weekly_reset_at",
    "month": "monthly_reset_at",
}
PERIOD_LABELS = {"day": "今日", "week": "本周", "month": "本月"}


class UsageLimitError(RuntimeError):
    def __init__(self, period: str, used_usd: float, limit_usd: float,
                 estimated_usd: float, resets_at: float):
        self.code = "AI_USAGE_LIMIT_EXCEEDED"
        self.status = 429
        self.period = period
        self.used_usd = round(used_usd, 6)
        self.limit_usd = round(limit_usd, 6)
        self.estimated_usd = round(estimated_usd, 6)
        self.remaining_usd = round(max(0.0, limit_usd - used_usd), 6)
        self.resets_at = resets_at
        self.message = f"{PERIOD_LABELS[period]} AI 使用额已不足"
        super().__init__(self.message)


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


def _preset_price(model: str | None) -> tuple[float, str]:
    """Flat price used when a call reports no tokens to price."""
    name = (model or "").lower()
    if name.startswith("hy-"):
        return optional_float("USAGE_PRICE_3D_PER_TASK", 0.5), SOURCE_PRESET
    if "image" in name:
        return optional_float("USAGE_PRICE_IMAGE_PER_IMAGE", 0.14), SOURCE_PRESET
    return 0.0, SOURCE_UNPRICED


def normalize_usage(payload: Any, model: str | None = None,
                    feature: str = "") -> dict[str, Any]:
    """Turn a provider response or usage payload into tokens and US dollars.

    The price is taken from the provider when the response carries one, otherwise
    it is computed as ``tokens x unit rate`` for the model that was called.
    """
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
    text_tokens, image_tokens, cached_tokens = input_token_details(payload)

    cost, field = find_cost(payload)
    if cost is not None:
        source = SOURCE_PROVIDER
    else:
        field = None
        cost = token_cost(
            model, input_tokens, output_tokens, text_tokens, image_tokens, cached_tokens,
        )
        source = SOURCE_TOKEN_PRICE if cost is not None else ""
        if cost is None:
            cost, source = _preset_price(model)
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
    numeric_type = "DOUBLE PRECISION" if is_postgres else "REAL"
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS auth_usage_limits (
            user_id TEXT PRIMARY KEY,
            daily_limit_usd {numeric_type},
            weekly_limit_usd {numeric_type},
            monthly_limit_usd {numeric_type},
            daily_reset_at {numeric_type},
            weekly_reset_at {numeric_type},
            monthly_reset_at {numeric_type},
            updated_by TEXT,
            updated_at {numeric_type} NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS auth_usage_reservations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            feature TEXT NOT NULL,
            action TEXT,
            model TEXT,
            estimated_usd {numeric_type} NOT NULL,
            actual_usd {numeric_type},
            status TEXT NOT NULL,
            created_at {numeric_type} NOT NULL,
            expires_at {numeric_type} NOT NULL,
            settled_at {numeric_type}
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS auth_usage_limit_audit (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            actor_id TEXT,
            operation TEXT NOT NULL,
            period TEXT,
            details TEXT,
            created_at {numeric_type} NOT NULL
        )
        """
    )
    _add_missing_columns(connection, is_postgres)
    _backfill_token_costs(connection, is_postgres)
    connection.commit()


def _usage_timezone() -> ZoneInfo:
    name = os.getenv("USAGE_LIMIT_TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown USAGE_LIMIT_TIMEZONE=%s; falling back to Asia/Tokyo", name)
        return ZoneInfo("Asia/Tokyo")


def _period_window(period: str, now: float) -> tuple[float, float]:
    local_now = datetime.fromtimestamp(now, timezone.utc).astimezone(_usage_timezone())
    if period == "day":
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "week":
        start = (local_now - timedelta(days=local_now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        end = start + timedelta(days=7)
    elif period == "month":
        start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12 else start.replace(month=start.month + 1)
        )
    else:
        raise ValueError(f"unsupported usage period: {period}")
    return start.timestamp(), end.timestamp()


def _limit_row(connection: Any, user_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    is_postgres = str(connection.__class__.__module__).startswith("psycopg")
    placeholder = "%s" if is_postgres else "?"
    suffix = " FOR UPDATE" if lock and is_postgres else ""
    row = connection.execute(
        f"SELECT * FROM auth_usage_limits WHERE user_id = {placeholder}{suffix}",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def _period_usage(connection: Any, user_id: str, start: float, now: float) -> tuple[float, float]:
    is_postgres = str(connection.__class__.__module__).startswith("psycopg")
    placeholder = "%s" if is_postgres else "?"
    used = connection.execute(
        f"""SELECT COALESCE(SUM(cost_usd), 0) AS total
            FROM auth_usage_events
            WHERE user_id = {placeholder} AND created_at >= {placeholder}
              AND created_at <= {placeholder}""",
        (user_id, start, now),
    ).fetchone()
    reserved = connection.execute(
        f"""SELECT COALESCE(SUM(estimated_usd), 0) AS total
            FROM auth_usage_reservations
            WHERE user_id = {placeholder} AND status = 'RESERVED'
              AND expires_at > {placeholder}""",
        (user_id, now),
    ).fetchone()
    return float(used["total"] or 0), float(reserved["total"] or 0)


def _limit_status(connection: Any, user_id: str, now: float,
                  row: dict[str, Any] | None = None) -> dict[str, Any]:
    row = row if row is not None else _limit_row(connection, user_id)
    periods: dict[str, dict[str, Any]] = {}
    for period in PERIODS:
        natural_start, natural_end = _period_window(period, now)
        reset_at = float((row or {}).get(RESET_COLUMNS[period]) or 0)
        start = max(natural_start, reset_at)
        used, reserved = _period_usage(connection, user_id, start, now)
        limit_value = (row or {}).get(LIMIT_COLUMNS[period])
        limit = float(limit_value) if limit_value is not None else None
        effective = used + reserved
        periods[period] = {
            "limit_usd": round(limit, 6) if limit is not None else None,
            "used_usd": round(used, 6),
            "reserved_usd": round(reserved, 6),
            "effective_used_usd": round(effective, 6),
            "remaining_usd": round(max(0.0, limit - effective), 6) if limit is not None else None,
            "period_started_at": start,
            "resets_at": natural_end,
            "manually_reset_at": reset_at or None,
        }
    return {
        "user_id": user_id,
        "timezone": str(_usage_timezone()),
        "periods": periods,
        "updated_at": float((row or {}).get("updated_at") or 0) or None,
    }


def _validate_limit(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1_000_000:
        raise ValueError("usage limit must be between 0 and 1000000 USD")
    return round(number, 6)


def get_usage_limits(user_id: str) -> dict[str, Any]:
    """Return the current daily, weekly, and monthly budget state for one account."""
    with database_connection() as connection:
        _ensure_usage_schema(connection)
        return _limit_status(connection, user_id, time.time())


def set_usage_limits(user_id: str, *, daily_limit_usd: float | None,
                     weekly_limit_usd: float | None,
                     monthly_limit_usd: float | None,
                     actor_id: str | None = None) -> dict[str, Any]:
    """Replace all three account limits. None means unlimited; zero blocks usage."""
    values = {
        "daily_limit_usd": _validate_limit(daily_limit_usd),
        "weekly_limit_usd": _validate_limit(weekly_limit_usd),
        "monthly_limit_usd": _validate_limit(monthly_limit_usd),
    }
    now = time.time()
    with database_connection() as connection:
        _ensure_usage_schema(connection)
        is_postgres = str(connection.__class__.__module__).startswith("psycopg")
        placeholder = "%s" if is_postgres else "?"
        connection.execute(
            f"""INSERT INTO auth_usage_limits
                (user_id, daily_limit_usd, weekly_limit_usd, monthly_limit_usd,
                 updated_by, updated_at)
                VALUES ({', '.join([placeholder] * 6)})
                ON CONFLICT(user_id) DO UPDATE SET
                    daily_limit_usd = excluded.daily_limit_usd,
                    weekly_limit_usd = excluded.weekly_limit_usd,
                    monthly_limit_usd = excluded.monthly_limit_usd,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at""",
            (
                user_id, values["daily_limit_usd"], values["weekly_limit_usd"],
                values["monthly_limit_usd"], actor_id, now,
            ),
        )
        connection.execute(
            f"""INSERT INTO auth_usage_limit_audit
                (id, user_id, actor_id, operation, period, details, created_at)
                VALUES ({', '.join([placeholder] * 7)})""",
            (
                uuid.uuid4().hex, user_id, actor_id, "SET_LIMITS", None,
                json.dumps(values, ensure_ascii=False), now,
            ),
        )
        connection.commit()
        return _limit_status(connection, user_id, now)


def reset_usage_limits(user_id: str, period: str,
                       actor_id: str | None = None) -> dict[str, Any]:
    """Reset a period's counter without deleting its immutable usage history."""
    selected = list(PERIODS) if period == "all" else [period]
    if any(item not in PERIODS for item in selected):
        raise ValueError("usage period must be day, week, month, or all")
    now = time.time()
    with database_connection() as connection:
        _ensure_usage_schema(connection)
        is_postgres = str(connection.__class__.__module__).startswith("psycopg")
        placeholder = "%s" if is_postgres else "?"
        connection.execute(
            f"""INSERT INTO auth_usage_limits (user_id, updated_by, updated_at)
                VALUES ({placeholder}, {placeholder}, {placeholder})
                ON CONFLICT(user_id) DO NOTHING""",
            (user_id, actor_id, now),
        )
        assignments = ", ".join(
            f"{RESET_COLUMNS[item]} = {placeholder}" for item in selected
        )
        connection.execute(
            f"""UPDATE auth_usage_limits
                SET {assignments}, updated_by = {placeholder}, updated_at = {placeholder}
                WHERE user_id = {placeholder}""",
            tuple([now] * len(selected) + [actor_id, now, user_id]),
        )
        connection.execute(
            f"""INSERT INTO auth_usage_limit_audit
                (id, user_id, actor_id, operation, period, details, created_at)
                VALUES ({', '.join([placeholder] * 7)})""",
            (uuid.uuid4().hex, user_id, actor_id, "RESET_USAGE", period, None, now),
        )
        connection.commit()
        return _limit_status(connection, user_id, now)


def _estimated_usage_cost(model: str | None, feature: str) -> float:
    """Amount reserved before a paid call, before its tokens are known."""
    estimated, source = _preset_price(model)
    if source == SOURCE_UNPRICED:
        estimated = optional_float("USAGE_RESERVE_VISION_PER_CALL_USD", 0.10)
        return round(max(0.0, estimated), 6)
    # Reference images make an edit call far more expensive than a plain
    # generation, so reserve against the token estimate as well.
    reference_images = 6 if feature == "image_to_3d" else 1
    token_estimate = estimated_call_cost(model, reference_images)
    if token_estimate is None:
        return round(max(0.0, estimated), 6)
    return round(max(0.0, max(estimated, token_estimate)), 6)


def reserve_usage(user: dict[str, Any] | None, feature: str, model: str | None,
                  action: str = "") -> str | None:
    """Atomically reserve estimated spend before a paid provider request."""
    user_id = str((user or {}).get("id") or "")
    if not user_id:
        return None
    now = time.time()
    with database_connection() as connection:
        _ensure_usage_schema(connection)
        is_postgres = str(connection.__class__.__module__).startswith("psycopg")
        if not is_postgres:
            connection.execute("BEGIN IMMEDIATE")
        row = _limit_row(connection, user_id, lock=True)
        if not row or all(row.get(LIMIT_COLUMNS[item]) is None for item in PERIODS):
            if not is_postgres:
                connection.commit()
            return None
        estimated = _estimated_usage_cost(model, feature)
        status = _limit_status(connection, user_id, now, row)
        for period in PERIODS:
            item = status["periods"][period]
            limit = item["limit_usd"]
            if limit is not None and item["effective_used_usd"] + estimated > limit + 1e-9:
                raise UsageLimitError(
                    period, item["effective_used_usd"], limit, estimated, item["resets_at"],
                )
        placeholder = "%s" if is_postgres else "?"
        reservation_id = uuid.uuid4().hex
        ttl = max(60.0, optional_float("USAGE_RESERVATION_TTL_SECONDS", 1800.0))
        connection.execute(
            f"""INSERT INTO auth_usage_reservations
                (id, user_id, feature, action, model, estimated_usd, actual_usd,
                 status, created_at, expires_at, settled_at)
                VALUES ({', '.join([placeholder] * 11)})""",
            (
                reservation_id, user_id, feature, action, model, estimated, None,
                "RESERVED", now, now + ttl, None,
            ),
        )
        connection.commit()
        return reservation_id


def release_usage_reservation(reservation_id: str | None) -> None:
    if not reservation_id:
        return
    try:
        with database_connection() as connection:
            _ensure_usage_schema(connection)
            placeholder = "%s" if str(connection.__class__.__module__).startswith("psycopg") else "?"
            connection.execute(
                f"""UPDATE auth_usage_reservations
                    SET status = 'RELEASED', settled_at = {placeholder}
                    WHERE id = {placeholder} AND status = 'RESERVED'""",
                (time.time(), reservation_id),
            )
            connection.commit()
    except Exception:
        logger.exception("Failed to release usage reservation id=%s", reservation_id)


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


def _stored_token_details(raw: Any) -> tuple[float, float, float]:
    """Read the token split that was stored with the original provider response."""
    if not raw:
        return 0.0, 0.0, 0.0
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0
    return input_token_details(payload)


def _backfill_token_costs(connection: Any, is_postgres: bool) -> None:
    """Reprice stored rows from their token counts so tokens and dollars agree.

    Rows that carry a price from the provider are left untouched, because that
    price is what the provider actually charged. The scan is skipped until the
    price configuration or the number of candidate rows changes.
    """
    placeholder = "%s" if is_postgres else "?"
    candidates = f"""COALESCE(cost_source, '') <> {placeholder}
              AND (COALESCE(input_tokens, 0) > 0 OR COALESCE(output_tokens, 0) > 0)"""
    counted = connection.execute(
        f"SELECT COUNT(*) AS total FROM auth_usage_events WHERE {candidates}",
        (SOURCE_PROVIDER,),
    ).fetchone()
    candidate_count = int(dict(counted)["total"])
    signature = pricing_signature()
    if (_backfill_state["signature"] == signature
            and _backfill_state["count"] == candidate_count):
        return
    _backfill_state["signature"] = signature
    _backfill_state["count"] = candidate_count
    rows = connection.execute(
        f"""SELECT id, model, feature, input_tokens, output_tokens, cost_usd, raw
            FROM auth_usage_events WHERE {candidates}""",
        (SOURCE_PROVIDER,),
    ).fetchall()
    for row in rows:
        data = dict(row)
        text_tokens, image_tokens, cached_tokens = _stored_token_details(data.get("raw"))
        cost = token_cost(
            data.get("model"),
            float(data.get("input_tokens") or 0),
            float(data.get("output_tokens") or 0),
            text_tokens, image_tokens, cached_tokens,
        )
        if cost is None:
            continue
        if abs(float(data.get("cost_usd") or 0.0) - cost) < 1e-9:
            continue
        connection.execute(
            f"""UPDATE auth_usage_events
                SET cost_usd = {placeholder}, cost_source = {placeholder}, cost_field = NULL
                WHERE id = {placeholder}""",
            (cost, SOURCE_TOKEN_PRICE, data["id"]),
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
                 payload: Any, action: str = "",
                 reservation_id: str | None = None) -> dict[str, Any]:
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
            if reservation_id:
                connection.execute(
                    f"""UPDATE auth_usage_reservations
                        SET status = 'SETTLED', actual_usd = {placeholder},
                            settled_at = {placeholder}
                        WHERE id = {placeholder} AND status = 'RESERVED'""",
                    (event["cost_usd"], time.time(), reservation_id),
                )
            connection.commit()
    except Exception:
        logger.exception(
            "Failed to persist usage event for feature=%s model=%s user_id=%s",
            feature, model, event["user_id"],
        )
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
        logger.exception("Failed to build the administrator usage report")
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
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "features": {},
            "sources": [],
        })
        feature = data["feature"]
        calls = int(data.get("calls") or 0)
        cost = float(data.get("cost_usd") or 0.0)
        input_tokens = int(data.get("input_tokens") or 0)
        output_tokens = int(data.get("output_tokens") or 0)
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
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["total_tokens"] += tokens
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)
        bucket["models"].append({
            "model": data.get("model") or "-",
            "calls": calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
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
        account["input_tokens"] += input_tokens
        account["output_tokens"] += output_tokens
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
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        })
        total["calls"] += calls
        total["input_tokens"] += input_tokens
        total["output_tokens"] += output_tokens
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
        "grand_input_tokens": sum(item["input_tokens"] for item in account_rows),
        "grand_output_tokens": sum(item["output_tokens"] for item in account_rows),
        "grand_total_tokens": sum(item["total_tokens"] for item in account_rows),
    }


def _probe() -> int:
    """Show how a provider response is priced.

    Usage: echo '{"usage":{"total_tokens":1200}}' | python -m vercel_app.usage
    """
    import sys

    raw = sys.stdin.read().strip()
    if not raw:
        print("请把接口返回的 JSON 粘进来，例如：")
        print("""echo '{"usage":{"total_tokens":1200}}' | python -m vercel_app.usage""")
        return 1
    try:
        payload = json.loads(raw)
    except ValueError as error:
        print(f"JSON 解析失败：{error}")
        return 1
    model = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    cost, field = find_cost(payload)
    normalized = normalize_usage(payload, model or None)
    print(f"模型：{model or '未指定'}")
    print(f"抓到费用：{cost}  字段：{field or '未找到'}")
    print(f"token：输入 {normalized['input_tokens']} / 输出 {normalized['output_tokens']}"
          f" / 合计 {normalized['total_tokens']}")
    print(f"计价方式：{normalized['cost_source']}")
    print(f"记账金额：{normalized['cost_usd']} 美元")
    if cost is None:
        print("提示：响应里没有费用字段，已按 token × 单价折算。")
        print("带上模型名可以查到对应单价，例如：")
        print("""echo '{"usage":{"total_tokens":1200}}' | python -m vercel_app.usage gpt-image-2.5-flare""")
    return 0


if __name__ == "__main__":
    raise SystemExit(_probe())
