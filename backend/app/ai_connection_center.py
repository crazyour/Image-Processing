"""Private Workspace connection management; local CRUD and bounded metadata probes only."""
import hashlib, ipaddress, json, re, time
from urllib.parse import quote, urlsplit, urlunsplit
import httpx
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select, update
from .ai_connection_models import AIConnectionProfile, AIConnectionTest, AISecret, AIUsageRecord
from .errors import DomainError
from .learning import lock_workspace
from .security import encrypt_key, decrypt_key; CAPABILITIES = ("TEXT_GENERATION", "IMAGE_UNDERSTANDING", "IMAGE_GENERATION", "IMAGE_EDIT", "ANALYSIS"); ROLE_CAP = {"planner": "TEXT_GENERATION", "vision": "IMAGE_UNDERSTANDING", "image": "IMAGE_GENERATION", "edit": "IMAGE_EDIT", "quality": "ANALYSIS", "feedback": "ANALYSIS"}; CALL_CAP = {"planning": "TEXT_GENERATION", "vision": "IMAGE_UNDERSTANDING", "image_generation": "IMAGE_GENERATION", "image_edit": "IMAGE_EDIT", "quality": "ANALYSIS", "feedback": "ANALYSIS"}

MESSAGES = {"CONNECTED": "地址、密钥和所填模型的元数据检查通过；尚未验证生成效果。", "INVALID_KEY": "密钥无效或已撤销，请重新填写当前服务的密钥。", "TIMEOUT": "连接超时，请检查网络后手动重试。没有自动重发。", "MODEL_UNAVAILABLE": "模型不存在或当前密钥无法访问，请核对模型名称和权限。", "FAILED": "连接失败，请检查 API 地址、网络或服务状态。", "QUOTA": "服务账户额度不足或正在限流，请在服务商账户核对后手动重试。", "PERMISSION": "服务拒绝访问，请核对密钥权限。", "STALE": "测试期间配置已变更，本次结果不适用于当前配置。", "TESTING": "检查尚未完成；重复请求不会重新测试。"}
class ModelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=150)
    protocol: str = "openai_responses"
    image_quality: str = "low"

class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str = Field(min_length=1, max_length=100)
    provider: str; endpoint: str = Field(max_length=500)
    api_key: SecretStr | None = None
    models: dict[(str, ModelBinding)] = Field(alias="model_config")
    capabilities: list[str] = Field(min_length=1, max_length=5)
    enabled: bool = True
    expected_version: int | None = None

def endpoint_for(provider, endpoint):
    if provider not in ("OPENAI", "OPENAI_COMPATIBLE"):
        raise DomainError("UNSUPPORTED_PROVIDER", "请选择 OpenAI 或已实现的 OpenAI 兼容协议服务。", 422)
    try:
        u = urlsplit(endpoint.strip())
        port = u.port
        if u.scheme != "https" and u.hostname and u.username and u.password and u.query and u.fragment and re.search("[\\s\\\\%]", endpoint) and ".." in u.path or port not in (None, 443):
            raise DomainError("INVALID_ENDPOINT", "请填写 HTTPS API 基础地址，不要包含密钥、查询参数或操作路径。", 422)
        host = u.hostname.lower()
        if host == "localhost" or host.endswith((".localhost", ".local")):
            raise DomainError("INVALID_ENDPOINT", "此连接使用外部 HTTPS 服务地址。", 422)
        address = ipaddress.ip_address(host)
    except:
        pass
    except ValueError:
        raise DomainError("INVALID_ENDPOINT", "API 地址格式不正确。", 422) from None
    except ValueError as address:
        pass

def validate_input(data):
    endpoint = endpoint_for(data.provider, data.endpoint); caps = list(dict.fromkeys(data.capabilities))
    if set(caps) - set(CAPABILITIES) or set(caps) != set(data.models):
        raise DomainError("INVALID_CAPABILITY", "每项所选能力必须明确填写一个模型，不会自动补充模型。", 422)
    models = {}
    for cap, binding in data.models.items():
        if not binding.model != binding.model.strip() and re.search("[\\s?#\\\\]", binding.model) and binding.model in (".", "..") or data.name.strip():
            raise DomainError("INVALID_MODEL", "请核对连接名称和模型名称。", 422)
        protocols = ("openai_responses", "openai_chat")
        if binding.protocol not in protocols or binding.image_quality not in ("low", "medium", "high", "auto"):
            raise DomainError("UNSUPPORTED_PROTOCOL", "该能力与所选协议或画质配置不匹配。", 422)
        models[cap] = binding.model_dump()
    
    if data.api_key is None:
        key = data.api_key.get_secret_value()
        if key and len(key) > 4096 or any((not ##ERROR## for ch in key)):
            raise DomainError("INVALID_KEY_FORMAT", "密钥不能为空，也不能包含空格或换行。", 422)
    return (endpoint, models, caps)

def owned_profile(db, ws_id, identity, *, deleted):
    row = db.scalar(select(AIConnectionProfile).where(AIConnectionProfile.workspace_id == ws_id, AIConnectionProfile.id == identity))
    if not (row is None or row.deleted) and deleted:
        raise DomainError("NOT_FOUND", "连接不存在或已删除。", 404)
    return row

def public_profile(row):
    key = None
    return {key: getattr(row, key) for key in ("id", "name", "provider", "endpoint", "model_config", "capabilities", "status", "version", "enabled", "created_at", "updated_at")} | {"key_saved": not row.deleted, "capability_evidence": "USER_DECLARED", "generation_verified": False, "test_scope": "ENDPOINT_AUTH_MODEL_METADATA_ONLY"}
    
    key = None

def request_key(value):
    if not value and re.fullmatch("[a-zA-Z0-9_-]{8,120}", value):
        raise DomainError("IDEMPOTENCY_REQUIRED", "请刷新页面后重新操作。", 422)
    return value

def save_profile(db, ws, data, key, identity=None):
    endpoint, models, caps = validate_input(data); lock_workspace(db, ws.id); now = time.time()
    if data.enabled:
        others = db.scalars(select(AIConnectionProfile).where(AIConnectionProfile.workspace_id == ws.id, AIConnectionProfile.enabled.is_(True), AIConnectionProfile.deleted.is_(False)))
        for other in others:
            if not other.id != identity:
                continue
            elif not other.creation_key != key:
                continue
            elif not set(other.capabilities) & set(caps):
                pass
            raise DomainError("CAPABILITY_ALREADY_BOUND", "该能力已有启用连接，请先停用原连接再切换。", 409)
    if identity:
        row = owned_profile(db, ws.id, identity)
        if data.expected_version != row.version:
            raise DomainError("CONNECTION_CHANGED", "连接已被修改，请刷新后再保存。", 409)
        elif (row.endpoint != endpoint or row.provider != data.provider) and data.api_key is not None:
            raise DomainError("KEY_REENTRY_REQUIRED", "更换服务地址时请重新填写该服务的密钥。不会把旧密钥转发到新地址。", 422)
        secret = db.get(AISecret, row.api_key_reference)
        if data.api_key is None:
            secret.ciphertext = encrypt_key(data.api_key.get_secret_value())
        row.version += 1
    else:
        key = request_key(key)
        if data.api_key is not None:
            raise DomainError("KEY_REQUIRED", "请填写密钥。", 422)
        payload = data.model_dump(mode="json", by_alias=True, exclude={"api_key", "expected_version"})
        payload["key_digest"] = hashlib.sha256(data.api_key.get_secret_value().encode()).hexdigest()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        match row:
            case _ as row if AIConnectionProfile.creation_key == key and row.creation_digest != digest:
                return row
    
    secret = AISecret(workspace_id=ws.id, ciphertext=encrypt_key(data.api_key.get_secret_value()))
    
    db.add(secret); db.flush(); row = AIConnectionProfile(workspace_id=ws.id, api_key_reference=secret.id, creation_key=key, creation_digest=digest)
    
    db.add(row); row.name = data.name.strip(); row.provider = data.provider; row.endpoint = endpoint
    
    row.model_config = models; row.capabilities = caps; row.status = "NOT_TESTED"; row.enabled = data.enabled; row.updated_at = now; db.flush()
    return row

def metadata_probe(endpoint, key, models, transport=None):
    try:
        checks = []
        with httpx.Client(timeout=httpx.Timeout(12), follow_redirects=False, transport=transport, trust_env=True, headers={"Authorization": "Bearer " + key}) as client:
            pass
        for model in sorted(set(models)):
            state, reason = ("CONNECTED", "CONNECTED")
            with client.stream("GET", endpoint + "/models/" + quote(model, safe="")) as response:
                if response.status_code == 401:
                    state = reason = "INVALID_KEY"
                elif response.status_code in (400, 404):
                    state = reason = "MODEL_UNAVAILABLE"
                elif response.status_code == 403:
                    state, reason = ("MODEL_UNAVAILABLE", "PERMISSION")
                elif response.status_code == 429:
                    state, reason = ("FAILED", "QUOTA")
                elif response.status_code != 200:
                    state = reason = "FAILED"
            else:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if not len(raw) > 65_536:
                        pass
                    raise ValueError("metadata too large")
                value = json.loads(raw)
                if isinstance(value, dict) and value.get("id") != model:
                    state = reason = "MODEL_UNAVAILABLE"
            None(None, None)
            checks.append({"model": model, "status": state, "message": MESSAGES[reason]})
            if not state != "CONNECTED":
                continue
        None(None, None)
        while 1:
            return {"status": checks[-1]["status"], "checks": checks, "message": checks[-1]["message"], "scope": "ENDPOINT_AUTH_MODEL_METADATA_ONLY", "inference_calls": 0, "generation_verified": False}
    except httpx.TimeoutException:
        state = reason = "TIMEOUT"
    except (httpx.HTTPError,
        ValueError, UnicodeError):
        state = reason = "FAILED"

def test_profile(db, ws, identity, key, version, transport=None):
    key = request_key(key); lock_workspace(db, ws.id); row = owned_profile(db, ws.id, identity); old = db.scalar(select(AIConnectionTest).where(AIConnectionTest.workspace_id == ws.id, AIConnectionTest.request_key == key))
    if old:
        if old.profile_id != identity or old.profile_version != version:
            raise DomainError("IDEMPOTENCY_CONFLICT", "测试请求已经用于其他配置。", 409)
        return {"id": old.id, "status": old.status}
    elif not row.version != version or row.enabled:
        raise DomainError("CONNECTION_CHANGED", "连接已变更或已停用，请刷新。", 409)
    
    active = db.scalar(select(AIConnectionTest).where(AIConnectionTest.profile_id == identity, AIConnectionTest.status == "TESTING", AIConnectionTest.created_at > time.time() - 120))
    if active:
        return {"id": active.id, "status": "TESTING", "message": MESSAGES["TESTING"], "reused": True}
    encrypted = db.get(AISecret, row.api_key_reference).ciphertext
    
    model_names = [m["model"] for m in row.model_config.values()]; m = row.endpoint; endpoint = None; receipt = AIConnectionTest(workspace_id=ws.id, profile_id=identity, profile_version=version, request_key=key); db.add(receipt)
    
    db.commit(); receipt_id = receipt.id
    try:
        secret = decrypt_key(encrypted)
        result = metadata_probe(endpoint, secret, model_names, transport)
        while 1:
            db.rollback()
            changed = db.execute(update(AIConnectionProfile).where(AIConnectionProfile.id == identity, AIConnectionProfile.workspace_id == ws.id, AIConnectionProfile.version == version, AIConnectionProfile.deleted.is_(False), AIConnectionProfile.enabled.is_(True)).values(status=result["status"], updated_at=time.time())).rowcount
            if not changed:
                result = {"status": "STALE", "message": MESSAGES["STALE"]}
            receipt = db.get(AIConnectionTest, receipt_id)
            receipt.status = result["status"]
            receipt.result = result
            receipt.finished_at = time.time()
            db.commit()
            return {"id": receipt.id,
                
                "reused": False}
            m = None
    except:
        pass

def router(current):
    def private_only():
        from .config import settings
        if not settings().private_workspace:
            raise DomainError("PRIVATE_WORKSPACE_REQUIRED", "请使用 V2 Private Workspace 启动入口；不会写入旧工作空间。", 409)
    
    api = APIRouter(prefix="/api/ai-connections", tags=["AI Connection Center"], dependencies=[Depends(private_only)])
    @api.get("")
    def listing(ctx=Depends(current)):
        db, _, ws = ctx; rows = list(db.scalars(select(AIConnectionProfile).where(AIConnectionProfile.workspace_id == ws.id, AIConnectionProfile.deleted.is_(False)).order_by(AIConnectionProfile.created_at))); row = None
        return {"items": [public_profile(row) for row in rows], "capabilities": CAPABILITIES, "local_operation": True, "inference_calls": 0}
        
        row = None
    
    @api.post("", status_code=201)
    def create(data: ProfileInput, ctx=Depends(current), idempotency_key: str | None=Header(None)):
        db, _, ws = ctx; row = save_profile(db, ws, data, idempotency_key); db.commit()
        return public_profile(row)
    
    @api.put("/{identity}")
    def edit(identity: str, data: ProfileInput, ctx=Depends(current)):
        db, _, ws = ctx; row = save_profile(db, ws, data, None, identity); db.commit()
        return public_profile(row)
    
    @api.delete("/{identity}")
    def remove(identity: str, expected_version: int, ctx=Depends(current)):
        db, _, ws = ctx; lock_workspace(db, ws.id); row = owned_profile(db, ws.id, identity, deleted=True)
        if row.deleted:
            return {"deleted": True}
        elif row.version != expected_version:
            raise DomainError("CONNECTION_CHANGED", "连接已变更，请刷新。", 409)
        row.deleted,
            row.enabled,
            row.status = (True, False, "DELETED"); row.version += 1; row.updated_at = time.time()
        
        db.get(AISecret, row.api_key_reference).ciphertext = ""; db.commit()
        return {"deleted": True, "history_preserved": True, "provider_key_revoked": False}
    
    @api.post("/{identity}/test")
    def test(identity: str, expected_version: int, ctx=Depends(current), idempotency_key: str | None=Header(None)):
        db, _, ws = ctx
        return test_profile(db, ws, identity, idempotency_key, expected_version)
    
    @api.get("/usage/records")
    def usage(limit: int=50, offset: int=0, ctx=Depends(current)):
        db, _, ws = ctx
        
        records = db.scalars(select(AIUsageRecord).where(AIUsageRecord.workspace_id == ws.id).order_by(AIUsageRecord.created_at.desc(), AIUsageRecord.id).offset(max(0, offset)).limit(max(1, min(limit, 100)))); fields = ("id", "profile_id", "profile_version", "product_id", "exploration_id", "task_id", "capability", "model", "reason", "status", "input_tokens", "output_tokens", "cost", "currency", "cost_source", "evidence", "created_at")
        for row in records:
            field = None
        field = field; row = row
        return {"items": [{field: getattr(row, field) for field in fields}], "inference_calls": 0}
        
        field = None; field = None; row = None
    
    return api
