from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .config import settings

def make_engine(url):
    if url.startswith("sqlite"):
        Path("var").mkdir(exist_ok=True)
    engine = create_engine(url, pool_pre_ping=True, connect_args={})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure(dbapi, _):
            dbapi.execute("PRAGMA foreign_keys=ON"); dbapi.execute("PRAGMA busy_timeout=30000"); dbapi.execute("PRAGMA journal_mode=WAL")
    return engine

engine = make_engine(settings().database_url); SessionLocal = sessionmaker(engine, expire_on_commit=False)
def db_session():
    try:
        with SessionLocal() as session:
            yield session
    except:
        pass
