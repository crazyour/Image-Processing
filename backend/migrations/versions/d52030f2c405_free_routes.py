"""Independent provider credentials and local daily quota reservations; no data reset."""

from alembic import op
import sqlalchemy as sa

revision = "d52030f2c405"
down_revision = "c41029e1b304"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False, index=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.UniqueConstraint("workspace_id", "provider"),
    )
    op.create_table(
        "free_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("account_scope", sa.String(100), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("reserved_units", sa.Float(), nullable=False),
        sa.UniqueConstraint("account_scope", "day"),
    )


def downgrade():
    op.drop_table("free_usage")
    op.drop_table("provider_credentials")
