"""Immutable personal style configurations.

Revision ID: a28097c9f302
Revises: 8830c8682427
"""

from alembic import op
import sqlalchemy as sa

revision = "a28097c9f302"
down_revision = "8830c8682427"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("style_profiles", sa.Column("config", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    with op.batch_alter_table("style_profiles") as batch:
        batch.drop_column("config")
