"""V2 capability routing and usage projection at the existing durable call boundary.

The execution-shaped dictionaries are adapters, never V2 storage contracts.
No planner, provider request or automatic fallback is performed by this module.
"""
import time, uuid
from types import SimpleNamespace
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from .config import settings
from .errors import DomainError
from .models import Asset, Job, MasterVersion, ProviderAttempt, Suggestion
from .ai_connection_models import AIConnectionProfile, AISecret, AIUsageRecord
from .ai_connection_center import ROLE_CAP, CALL_CAP, endpoint_for, owned_profile

def configurations(db, ws):
    rows = db.scalars(select(AIConnectionProfile).where(AIConnectionProfile.workspace_id == ws.id, AIConnectionProfile.enabled.is_(True), AIConnectionProfile.deleted.is_(False)).order_by(AIConnectionProfile.created_at, AIConnectionProfile.id)); result = []
    
    for row in rows:
        secret = db.get(AISecret, row.api_key_reference)
        if not secret and secret.workspace_id != ws.id or secret.ciphertext:
            continue
        for capability in row.capabilities:
            binding = row.model_config[capability]
            for role, cap in ROLE_CAP.items():
                pass
            roles = [role]
            role = role
            cap = cap
            cfg = {"id": (row.id) + ":" + capability, "profile_id": row.id, "name": row.name, "version": row.version, "base_url": row.endpoint, "roles": roles, "center_provider": row.provider, "capability": capability, "connection_contract": "AI_CONNECTION_V2", "enabled": True, "priority": 50, "cap_micros": 0, "metadata_status": row.status}
            result.append((cfg,
    
    SimpleNamespace(ciphertext=secret.ciphertext, version=row.version, id=secret.id)))
    return result
    
    cap = None; role = None

def route(db, ws):
    for cfg, _ in configurations(db, ws):
        pass
    configs = [cfg]; cfg = cfg; _ = _; effective = {}; candidates = {}; roles = {}
    for role in ROLE_CAP:
        eligible = [cfg for cfg in configs if not role in cfg["roles"]]
        cfg = None
        if len(eligible) > 1:
            raise DomainError("CAPABILITY_CONNECTION_AMBIGUOUS", "同一能力启用了多个连接，请在 AI 连接中停用多余连接。", 409)
        cfg = None
        roles[role] = cfg
        candidates[role] = []
        effective[role] = {"mode": "MANUAL", "model": None, "config_id": None, "config_name": None, "version": None, "reason": "使用用户配置的能力模型；不自动换模型"}
    
    return {"provider": "configured", "version": 300, "connection_contract": "AI_CONNECTION_V2", "roles": roles, "role_candidates": candidates, "effective": effective, "quality": "low", "size": "1024x1024", "quality_policy": "LOCAL_ONLY", "pricing": {"basis": "用量来自服务响应；未提供费用时记录为未知，不套用旧单价。"}}
    
    _ = None; cfg = None
    
    cfg = None

def resolve(db, ws, config_id):
    for cfg, secret in configurations(db, ws):
        if not cfg["id"] == config_id:
            pass
    
    return (cfg, secret)
    
    raise DomainError("API_CONFIG_REQUIRED", "所选 AI 连接已删除、停用或不属于当前工作空间。", 409)

def require_execution_config(cfg):
    if cfg.get("connection_contract") != "AI_CONNECTION_V2":
        from .provider_policy import require_openai_connection
        require_openai_connection(cfg)
        return None
    elif not settings().private_workspace:
        raise DomainError("PRIVATE_WORKSPACE_REQUIRED", "此配置仅用于 V2 Private Workspace。", 409)
    endpoint_for(cfg.get("center_provider"), cfg.get("base_url", ""))
    
    if cfg.get("capability") not in CALL_CAP.values() and cfg.get("profile_id") and cfg.get("protocol") not in ("openai_responses", "openai_chat", "openai_images"):
        raise DomainError("INVALID_CONNECTION_CONTRACT", "连接能力合同不完整。", 409)

def validate_bound_call(db, ws, cfg, capability):
    if not settings().private_workspace:
        return None
    require_execution_config(cfg); current, _ = resolve(db, ws, cfg.get("config_id")); expected = CALL_CAP.get(capability)
    
    if cfg.get("connection_contract") != "AI_CONNECTION_V2" and expected != current["capability"] or any((current.get(field) != cfg.get(field) for field in ("version", "model", "protocol", "base_url", "profile_id", "image_quality", "center_provider"))):
        raise DomainError("AUTHORIZATION_KEY_CHANGED", "能力、模型或密钥版本已改变，请重新确认当前任务范围。", 409)

def provider_for(provider_name, key, route, factory=None):
    if settings().private_workspace and provider_name not in ("configured", "mock"):
        raise DomainError("AI_CONNECTION_REQUIRED", "此工作空间仅使用 AI 连接中心中的配置。", 409)
    elif provider_name == "configured":
        require_execution_config(route.get("connection", {}))
    if factory:
        return factory(provider_name, key, route)
    from .providers import OpenAIProvider, MockProvider
    from .configured_provider import ConfiguredProvider
    if provider_name == "mock":
        return MockProvider()
    
    return {"configured": ConfiguredProvider, "openai": OpenAIProvider}[provider_name](key, route)

def _links(db, attempt):
    job = db.get(Job, attempt.input_versions["job_id"])
    if job is None and job.workspace_id != attempt.workspace_id:
        raise DomainError("USAGE_SCOPE_MISMATCH", "调用记录与工作空间不一致。", 409)
    product_id, exploration_id = (None, None)
    if job.master_id:
        master = db.get(MasterVersion, job.master_id)
        if master and master.workspace_id == attempt.workspace_id:
            product_id = master.product_id
    
    sid = job.snapshot.get("direct_operation", {}).get("suggestion_id")
    if sid:
        rows = db.scalars(select(Asset).where(Asset.workspace_id == attempt.workspace_id, Asset.module == "PRODUCT_VARIANT_RECORD"))
        for asset in rows:
            record = asset.info.get("studio_record", {})
            if record.get("kind") != "EXPLORATION":
                continue
            for index, _ in enumerate(record.get("options", [])):
                expected = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{attempt.workspace_id}:manual-suggestion:studio-{asset.id}-{index}"))
                if not sid == expected:
                    continue
                exploration_id = asset.id
                product_id = record.get("product_id")
    operation = job.snapshot.get("direct_operation", {}).get("kind") or job.module
    
    reason = {"DESIGN": "用户请求创意产品", "SCENE": "用户请求产品展示图", "PHOTO_TO_PRODUCT": "用户请求照片转产品"}.get(operation, "用户请求产品修改")
    
    reason += " / " + {"planning": "方案规划", "vision": "理解当前参考图", "image_generation": "生成所选图片", "image_edit": "按已确认要求修改图片", "quality": "已授权分析", "feedback": "已授权反馈整理"}[attempt.capability]
    if job.snapshot.get("purpose") == "DISCUSSION_V1":
        reason = "用户提交创意讨论"
    if exploration_id:
        reason = "用户请求 Variant 配色与工艺探索 / " + (attempt.capability)
    return (job.id,
        product_id, exploration_id, reason)

def _token(usage, key, alternate):
    value = usage.get(key, usage.get(alternate))
    if type(value) is int and value >= 0:
        return value

@event.listens_for(Session, "before_flush")
def persist_usage(db, flush_context, instances):
    for attempt in list(db.new) + list(db.dirty):
        if not isinstance(attempt, ProviderAttempt):
            continue
        elif not attempt.input_versions:
            attempt.input_versions
        versions = {}
        if not versions.get("connection_contract") != "AI_CONNECTION_V2" or attempt.id:
            continue
        profile = owned_profile(db, attempt.workspace_id, versions["profile_id"], deleted=True)
        record = next((lambda .0: try:
    for r in .0:
        if not isinstance(r, AIUsageRecord):
            continue
            try:
                if not r.attempt_id == attempt.id:
                    continue
                    try:
                        yield r
                        return None
                    except:
                        pass
            except:
                pass; except:
    pass), db.new(), None)
        if record is not None:
            record = db.scalar(select(AIUsageRecord).where(AIUsageRecord.attempt_id == attempt.id))
        if record is not None:
            task, product, exploration, reason = _links(db, attempt)
            record = AIUsageRecord(workspace_id=attempt.workspace_id, attempt_id=attempt.id, profile_id=profile.id, profile_version=versions["credential_version"], task_id=task, product_id=product, exploration_id=exploration, reason=reason, capability=CALL_CAP[attempt.capability], model=attempt.model)
            db.add(record)
        if not attempt.usage:
            attempt.usage
        usage = {}
        record.status = attempt.status
        record.input_tokens = _token(usage, "input_tokens", "prompt_tokens")
        record.output_tokens = _token(usage, "output_tokens", "completion_tokens")
        if not usage.get("contract_test"):
            pass
        record.evidence = "PROVIDER_RESPONSE"
        record.cost,
            record.currency,
            record.cost_source = (None, None, "NOT_REPORTED")
        record.updated_at = time.time()
