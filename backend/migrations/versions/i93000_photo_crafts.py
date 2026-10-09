"""Photo crafts: additive private tables, no historical row rewrites."""
from alembic import op
from app.craft_models import CraftTemplate, CraftDesign

revision='i93000_photo_crafts'
down_revision='h92700_design_discussions'
branch_labels=depends_on=None


def upgrade():
    CraftTemplate.__table__.create(op.get_bind(),checkfirst=True)
    CraftDesign.__table__.create(op.get_bind(),checkfirst=True)


def downgrade():
    # Keep customer work; explicit archival is required before a destructive rollback.
    raise RuntimeError('Photo craft data must be archived before removing these tables')
