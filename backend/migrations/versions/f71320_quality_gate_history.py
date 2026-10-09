"""Move legacy QA failures out of the ordinary selectable queue.

The generated files and their version lineage remain untouched.  Human-kept
work is also left untouched; this migration only corrects old machine states
that exposed a failed candidate as if it were ready for normal selection.
"""

from alembic import op
import json
import sqlalchemy as sa


revision = "f71320_quality_gate_history"
down_revision = "e61010_release_zones"
branch_labels = depends_on = None


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}


def upgrade():
    conn = op.get_bind()
    assets = sa.table(
        "assets",
        sa.column("id", sa.String),
        sa.column("state", sa.String),
        sa.column("info", sa.JSON),
        sa.column("deleted", sa.Boolean),
    )
    rows = conn.execute(
        sa.select(assets.c.id, assets.c.state, assets.c.info).where(
            assets.c.deleted.is_(False),
            assets.c.state.in_(("READY_FOR_SELECTION", "LATER")),
        )
    ).mappings()
    for row in rows:
        info = _json(row["info"])
        qa_status = (info.get("qa_result") or {}).get("qa_status")
        rejected = bool(info.get("automatic_quality_rejected"))
        geometry_failed = (info.get("check") or {}).get("geometry_status") == "FAIL"
        if qa_status not in ("REPAIRABLE", "FAIL", "HARD_FAIL") and not rejected and not geometry_failed:
            continue
        info = {
            **info,
            "automatic_quality_rejected": True,
            "quality_gate_status": "NEEDS_HUMAN_DECISION",
            "legacy_quality_state_migrated": True,
        }
        conn.execute(
            assets.update().where(assets.c.id == row["id"]).values(
                state="NEEDS_HUMAN_DECISION", info=info
            )
        )


def downgrade():
    # Restoring the old, misleading READY state would make failed work look
    # approved.  Keep the safe state and only remove the migration marker.
    conn = op.get_bind()
    assets = sa.table(
        "assets",
        sa.column("id", sa.String),
        sa.column("state", sa.String),
        sa.column("info", sa.JSON),
    )
    rows = conn.execute(
        sa.select(assets.c.id, assets.c.info).where(
            assets.c.state == "NEEDS_HUMAN_DECISION"
        )
    ).mappings()
    for row in rows:
        info = _json(row["info"])
        if not info.pop("legacy_quality_state_migrated", False):
            continue
        conn.execute(
            assets.update().where(assets.c.id == row["id"]).values(info=info)
        )
