"""Explicit employee contribution and signed, reversible company experience packages."""

from alembic import op
import sqlalchemy as sa

revision = "c41029e1b304"
down_revision = "b39018d0a203"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "hub_peers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False, index=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_seen", sa.Float()),
    )
    op.create_table(
        "experience_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.UniqueConstraint("workspace_id", "content_hash"),
    )
    op.create_table(
        "publisher_trust",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False, index=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("public_key", sa.String(100), nullable=False),
        sa.UniqueConstraint("workspace_id", "fingerprint"),
    )


def downgrade():
    op.drop_table("hub_peers")
    op.drop_table("publisher_trust")
    op.drop_table("experience_packages")
