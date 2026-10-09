"""Preserve existing rows and files; classify old data conservatively."""
from alembic import op
import sqlalchemy as sa
from app.data_zones import classify
import json

revision = 'e61010_release_zones'
down_revision = 'd52030f2c405'
branch_labels = depends_on = None

def upgrade():
    for name in ('jobs', 'assets'):
        op.add_column(name, sa.Column('data_zone', sa.String(20), nullable=False, server_default='PRODUCTION'))
        op.add_column(name, sa.Column('needs_confirmation', sa.Boolean(), nullable=False, server_default=sa.false()))
    conn = op.get_bind()
    jobs = {}
    for row in conn.execute(sa.text('SELECT id,snapshot FROM jobs')).mappings():
        snap = json.loads(row['snapshot']) if isinstance(row['snapshot'], str) else row['snapshot']
        zone, pending = classify(snapshot=snap, legacy=True)
        jobs[row['id']] = (zone, pending)
        conn.execute(sa.text('UPDATE jobs SET data_zone=:z,needs_confirmation=:p WHERE id=:id'), dict(z=zone,p=pending,id=row['id']))
    for row in conn.execute(sa.text('SELECT id,job_id,info FROM assets')).mappings():
        info = json.loads(row['info']) if isinstance(row['info'], str) else row['info']
        zone, pending = classify(info=info, legacy=True)
        if zone != 'TEST' and row['job_id'] in jobs:
            zone, pending = jobs[row['job_id']]
        conn.execute(sa.text('UPDATE assets SET data_zone=:z,needs_confirmation=:p WHERE id=:id'), dict(z=zone,p=pending,id=row['id']))

def downgrade():
    for name in ('assets', 'jobs'):
        op.drop_column(name, 'needs_confirmation')
        op.drop_column(name, 'data_zone')
