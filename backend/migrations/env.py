from alembic import context
from app.config import settings
from app.db import make_engine
from app.models import Base

config = context.config
if context.is_offline_mode():
    context.configure(url=settings().database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    migration_engine = make_engine(settings().database_url)
    with migration_engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, render_as_batch=connection.dialect.name == "sqlite"
        )
        with context.begin_transaction():
            context.run_migrations()
    migration_engine.dispose()
