"""R2 private drafts and discussion turns; existing rows stay unchanged."""
from alembic import op
from app.models import DesignDraft, DesignDiscussionTurn

revision = 'h92700_design_discussions'
down_revision = 'g81200_provider_operations'
branch_labels = depends_on = None


def upgrade():
    DesignDraft.__table__.create(op.get_bind(), checkfirst=True)
    DesignDiscussionTurn.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    DesignDiscussionTurn.__table__.drop(op.get_bind(), checkfirst=True)
    DesignDraft.__table__.drop(op.get_bind(), checkfirst=True)
