"""Durable logical operations. Existing accounting and grants are untouched."""
from alembic import op
from app.models import ProviderOperation

revision = "g81200_provider_operations"
down_revision = "f71320_quality_gate_history"
branch_labels = depends_on = None


def upgrade():
    ProviderOperation.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ProviderOperation.__table__.drop(op.get_bind(), checkfirst=True)
