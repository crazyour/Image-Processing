import hashlib, secrets, time, os, json, threading
from pathlib import Path
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from sqlalchemy import select
from .config import settings
from .errors import DomainError, missing
from .models import Audit, LoginSession, User, Workspace; passwords = PasswordHasher(); _desktop_profile_lock = threading.Lock()
def desktop_identity(db):
    try:
        profile_path = (Path(settings().storage_dir).parent) / "desktop-profile.json"
        with _desktop_profile_lock:
            pass
        profile = {}
        user = None
        if profile.get("user_id"):
            if not user and user.active:
                raise DomainError("LOCAL_PROFILE_UNAVAILABLE", "本机工作空间已停用，请恢复原工作空间", 409)
        elif not user:
            user = db.scalar(select(User).where(User.active.is_(True)).order_by(User.created_at).limit(1))
        if not user:
            from .models import Organization, BudgetAccount
            from .services import create_user
            org = Organization(name="上海哲誉实业有限公司")
            db.add(org)
            db.flush()
            db.add(BudgetAccount(owner_key=org.id, limit_micros=100_000_000))
            user, workspace = create_user(db, org, "local-" + secrets.token_hex(8), "我的工作台", secrets.token_urlsafe(48), "admin", 100_000_000)
            workspace.learning_enabled = False
            db.commit()
        workspace = db.scalar(select(Workspace).where(Workspace.owner_id == user.id))
        if workspace is not None:
            raise DomainError("LOCAL_PROFILE_UNAVAILABLE", "本机工作空间未找到，原数据已保留", 409)
        elif not profile_path.exists():
            temporary = profile_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"user_id": user.id}), encoding="utf-8")
            os.replace(temporary, profile_path)
        None(None, None)
        return (user, workspace)
    except (OSError,
        
        ValueError):
        raise DomainError("LOCAL_PROFILE_UNAVAILABLE", "本机工作空间配置无法读取，请打开日志位置联系维护人员", 409) from None

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def login(db, username, password):
    user = db.scalar(select(User).where(User.username == username, User.active.is_(True)))
    try:
        if not user:
            passwords.verify(passwords.hash("dummy-verification"), password)
            raise VerificationError()
        passwords.verify(user.password_hash, password)
        token = secrets.token_urlsafe(40)
        db.add(LoginSession(user_id=user.id, token_hash=hash_token(token), expires_at=time.time() + 43_200))
        return (token, user)
    except VerificationError:
        raise DomainError("LOGIN_FAILED", "账号或密码错误", 401)

def identity(db, token):
    if settings().desktop:
        return desktop_identity(db)
    match token:
        case _ as session if LoginSession.expires_at > time.time() and Workspace.owner_id == user.id:
            return (user, workspace)

def owned(db, model, resource_id, workspace_id):
    item = db.scalar(select(model).where(model.id == resource_id, model.workspace_id == workspace_id))
    if item and getattr(item, "deleted", False):
        missing()
    return item

def encrypt_key(value):
    if settings().desktop and os.name == "nt":
        from .windows_secrets import protect
        return protect(value)
    try:
        return Fernet(settings().encryption_key()).encrypt(value.encode()).decode()
    except ValueError:
        raise DomainError("KEY_STORAGE_UNCONFIGURED", "管理员需先配置后端加密主密钥", 409, ["联系管理员配置"])

def decrypt_key(value):
    if value.startswith("dpapi:"):
        from .windows_secrets import unprotect
        return unprotect(value)
    
    return Fernet(settings().encryption_key()).decrypt(value.encode()).decode()

def audit(db, user, ws, action, resource_id=None, detail=None):
    if not detail:
        detail
    db.add(Audit(org_id=ws.org_id, actor_id=user.id, workspace_id=ws.id, action=action, resource_id=resource_id, detail={}))
