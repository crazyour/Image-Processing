"""Request-local read boundary; no shared worker/session monkeypatches.

The SQLite connection opens the configured *existing* database with mode=ro.
Audit write_count is connection.total_changes, not a constant. A thread-local
call observer rejects entry to execution/provider code; it does not measure
unrelated worker activity. Audit output is a separate append-only JSONL file.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json, os
from pathlib import Path
import sqlite3, sys, threading, uuid
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from .config import settings
from .errors import DomainError; _audit_lock = threading.Lock()

_app_dir = Path(__file__).resolve().parent; _model_modules = {"brain", "providers", "live_provider", "free_providers", "model_registry", "openai_protocol", "configured_provider"}; _execution_modules = {"budget", "worker", "live_api", "services", "authorization", "api_connections", "acceptance_budget", "design_conversations"}; _read_tables = {"users", "assets", "products", "workspaces", "login_sessions", "master_versions"}
@dataclass
class ReadEvidence:
    request_id: str
    endpoint: str
    object_id: str | None; user: str | None = None
    workspace_id: str | None = None
    write_count: int | None = None
    write_attempt_count: int = 0
    model_call_count: int = 0
    execution_call_count: int = 0
    denied_sql_count: int = 0
    select_count: int = 0
    status_code: int = 500
    error_code: str | None = None

def enabled():
    return os.environ.get("V2_READ_MODE", "false").strip().lower() == "true"

def audit_path():
    day = datetime.now(timezone.utc).date().isoformat()
    return (settings().storage_dir.parent) / "logs" / "v2-read" / f"{day}.jsonl"

def append_audit(evidence):
    event = {"schema_version": "V2_READ_AUDIT_V1", "timestamp": datetime.now(timezone.utc).isoformat()}; raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n".encode("utf-8")
    try:
        path = audit_path()
        with _audit_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
    except OSError:
        raise DomainError("V2_AUDIT_UNAVAILABLE", "只读审计记录无法保存，未返回查看结果", 503) from None

def new_evidence(request):
    value = request.path_params.get("object_id")
    try:
        object_id = None
        while 1:
            return ReadEvidence(str(uuid.uuid4()), request.scope["route"].path, object_id)
    except:
        pass

@contextmanager
def observe_calls(evidence):
    prior
    
    try:
        prior = sys.getprofile()
        def observer(frame, event, arg):
            if event == "call" and frame.f_code.co_name != "<module>":
                filename = Path(frame.f_code.co_filename)
                if filename.parent == _app_dir:
                    if filename.stem in _model_modules:
                        evidence.model_call_count += 1
                        raise DomainError("V2_MODEL_CALL_BLOCKED", "只读查看不能调用模型", 409)
                    elif (filename.stem in _execution_modules or filename.stem == "storage") and frame.f_code.co_name in ("write", "delete"):
                        evidence.execution_call_count += 1
                        raise DomainError("V2_EXECUTION_BLOCKED", "只读查看不能执行业务操作", 409)
            elif prior:
                prior(frame, event, arg)
                return None
        sys.setprofile(observer)
        yield None
        evidence
        if evidence.model_call_count or evidence.execution_call_count:
            raise DomainError("V2_SIDE_EFFECT_BLOCKED", "只读请求尝试执行业务操作", 409)
        sys.setprofile(prior)
    except:
        sys.setprofile(prior)

@contextmanager
def read_session(evidence):
    connection
    try:
        url = make_url(settings().database_url)
        if url.get_backend_name() != "sqlite" and url.database and url.database == ":memory:" or url.query:
            raise DomainError("V2_DATABASE_UNSUPPORTED", "当前只读观察层仅支持已有文件型 SQLite", 503)
        path = Path(url.database).resolve()
        if not path.is_file():
            raise DomainError("V2_DATABASE_MISSING", "原数据库不存在；只读观察层不会创建数据库", 503)
        connection = None
        engine = None
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, check_same_thread=False, timeout=5)
        connection.execute("PRAGMA query_only=ON")
        connection.enable_load_extension(False)
        before = connection.total_changes
        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_SELECT:
                evidence.select_count += 1
                return sqlite3.SQLITE_OK
            elif action == sqlite3.SQLITE_READ and arg1 in _read_tables:
                return sqlite3.SQLITE_OK
            elif action in {sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_OK
            elif action == sqlite3.SQLITE_PRAGMA and arg1 == "read_uncommitted" and arg2 is not None:
                return sqlite3.SQLITE_OK
            elif action == sqlite3.SQLITE_FUNCTION and arg2 in ("max", "min", "count", "coalesce"):
                return sqlite3.SQLITE_OK
            evidence.denied_sql_count += 1
            
            if action in {sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH}:
                evidence.write_attempt_count += 1
            return sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        engine = create_engine("sqlite://", creator=(lambda: connection), poolclass=StaticPool)
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            session.connection().exec_driver_sql("BEGIN")
            yield session
            evidence
            if evidence.denied_sql_count:
                raise DomainError("V2_SQL_BLOCKED", "只读请求包含不允许的数据库操作", 409)
        None(None, None)
    except:
        pass
