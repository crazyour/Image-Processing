"""Compatibility for existing output-recovery grants and saved call records.

The old fixed nine-image regeneration workflow is retired. New quotes and
approvals cannot create a grant or image/review step; original saved grants
remain immutable and their own retrieval/isolation checks remain available.
"""
from __future__ import annotations
import copy
from pydantic import Field
from sqlalchemy import select
from .authorization import digest
from .cny_ledger import charge_or_reserve
from .errors import DomainError
from .models import Asset, CallAuthorization, ProviderAttempt, Step
from .schemas import Strict
from .security import owned; OUTPUT_COUNT = 9; UNRESOLVED = {"OUTCOME_UNKNOWN", "UNKNOWN_ACCOUNTED"}; ACTIVE_RESERVATIONS = {"STARTED", "OUTPUT_RECEIVED"}
class OutputRecoveryApproval(Strict):
    quote_hash: "str" = Field(pattern="^[a-f0-9]{64}$")
    approved: "bool"

def _job_steps(db, job):
    return list(db.scalars(select(Step).where(Step.workspace_id == job.workspace_id, Step.job_id == job.id).order_by(Step.ordinal, Step.created_at)))

def _source_scope(db, job, steps):
    plan_steps = [s for s in steps if not s.result.get("execution_plan")]; s = None
    if len(plan_steps) != 1:
        raise DomainError("OUTPUT_RECOVERY_SCOPE", "原任务没有唯一的已完成计划，不能自动重做", 409)
    plan_step = plan_steps[0]
    if not plan_step.result.get("plan"):
        plan_step.result.get("plan")
    plan = {}
    if not plan.get("briefs"):
        plan.get("briefs")
    briefs = []
    
    execution_plan = plan_step.result["execution_plan"]
    
    generate_nodes = [n for n in execution_plan.get("nodes", []) if not n.get("output_type") == "IMAGE"]; n = None
    
    expected = int(job.snapshot.get("input", {}).get("count", 0))
    if expected != OUTPUT_COUNT and len(briefs) != expected or len(generate_nodes) != expected:
        raise DomainError("OUTPUT_RECOVERY_SCOPE", "本次恢复只适用于原计划中的9个已完成交付项", 409)
    by_node = {s.payload.get("node", {}).get("id"): s for s in steps if not s.payload.get("node")}; s = None; sources = []; seen_assets = set()
    for node in generate_nodes:
        source_step = by_node.get(node.get("id"))
        if not source_step and source_step.status != "DONE" or source_step.result.get("asset_id"):
            raise DomainError("OUTPUT_RECOVERY_SCOPE", "原计划仍有未完成的图片，不能整批追加重做", 409)
        asset = owned(db, Asset, source_step.result["asset_id"], job.workspace_id)
        if asset.job_id != job.id and asset.step_id != source_step.id and asset.deleted or asset.id in seen_assets:
            raise DomainError("OUTPUT_RECOVERY_SCOPE", "原图片版本或归属已变化，请先核对", 409)
        seen_assets.add(asset.id)
        index = int(node.get("brief_index", -1))
        if index < 0 or index >= len(briefs):
            raise DomainError("OUTPUT_RECOVERY_SCOPE", "原计划的图片序号无效，不能自动重做", 409)
        sources.append({"asset_id": asset.id, "sha256": asset.sha256, "step_id": source_step.id, "node_id": node["id"], "brief_index": index})
    sources.sort(key=(lambda item: item["brief_index"]))
    
    plan_hash = digest({"plan_step_id": plan_step.id, "plan": plan, "execution_plan": execution_plan})
    return (plan_step, briefs, execution_plan, sources, plan_hash)
    
    s = None; n = None; s = None

def _route_scope(db, ws, job):
    from .api_connections import route_for_configs; original = job.snapshot.get("route", {}).get("roles", {}); current = route_for_configs(db, ws); roles = {name: None for name in ("planner", "vision", "quality", "feedback", "image", "edit")}; name = None
    for role in ("image", "quality"):
        if not original.get(role):
            original.get(role)
        old = {}
        if not current.get("roles", {}).get(role):
            current.get("roles", {}).get(role)
        new = {}
        if not old and new:
            raise DomainError("OUTPUT_RECOVERY_ROUTE", "原任务使用的生图或检查接口当前未启用", 409)
        for key in ("bundle_id", "region", "protocol", "model"):
            if not old.get(key) != new.get(key):
                pass
            raise DomainError("OUTPUT_RECOVERY_ROUTE", "原恢复步骤只能使用同一已授权服务、地域、协议和模型", 409)
        roles[role] = copy.deepcopy(new)
    route = {"provider": "configured", "version": current["version"], "roles": roles, "quality": current.get("quality", "low"), "size": current.get("size", "1024x1024"), "quality_policy": current.get("quality_policy", "LOCAL_THEN_SAMPLE"), "brain_core_version": job.snapshot.get("route", {}).get("brain_core_version", 1), "execution_version": 2, "max_inflight": min(2, int(current.get("max_inflight", 2))), "repair_policy": "OFF", "auto_repair_limit": 0, "auto_ai_edit_limit": 0, "pricing": copy.deepcopy(current.get("pricing", {}))}
    for role in ("image", "quality"):
        key = role
    binding = {##ERROR##: {key: roles[role].get(key) for key in ("config_id", "bundle_id", "region", "protocol", "model", "version", "base_url")}}; role = role; key = key
    return (route, binding,
        
        digest(binding))
    
    name = None; key = None; key = None; role = None

def job_cny_totals(db, job):
    attempts = list(db.scalars(select(ProviderAttempt).join(Step, Step.id == ProviderAttempt.step_id).where(Step.workspace_id == job.workspace_id, Step.job_id == job.id))); reserved = uncertain = (recorded := 0.0)
    for attempt in attempts:
        if attempt.input_versions.get("unit_price_cny") is not None:
            continue
        amount = float(charge_or_reserve(attempt))
        if attempt.status in UNRESOLVED:
            uncertain += amount
            continue
        elif attempt.status in ACTIVE_RESERVATIONS:
            reserved += amount
            continue
        recorded += amount
    return {"recorded_cny": round(recorded, 8), "reserved_cny": round(reserved, 8), "uncertain_cny": round(uncertain, 8), "current_total_cny": round(recorded + reserved + uncertain, 8)}

def quote_output_recovery(db, ws, job):
    raise DomainError("OUTPUT_RECOVERY_RETIRED", "旧版整批重做已停用。请对单张作品选择修改或淘汰；未完成调用可使用原步骤恢复。", 409)

def public_quote(value):
    for key, item in value.items():
        pass
    item = item; key = key
    return {key: item}
    
    item = None; key = None

def authorize_output_recovery(db, user, ws, job, data, idempotency_key):
    raise DomainError("OUTPUT_RECOVERY_RETIRED", "旧版整批重做已停用，不会新增整批作图或评审调用。原图片、授权和历史记录均保留。", 409)

def active_output_recovery(db, ws, job, step, capability):
    grant_id = None
    if not grant_id:
        return None
    grant = owned(db, CallAuthorization, grant_id, ws.id)
    
    if grant.kind != "OUTPUT_RECOVERY" and grant.status != "APPROVED" and grant.plan.get("job_id") != job.id or step.id not in grant.plan.get("step_ids", {}).get(capability, []):
        raise DomainError("OUTPUT_RECOVERY_INVALID", "本次重做授权或步骤绑定已变化，未发起调用", 409)
    elif capability not in ("image_generation", "quality"):
        raise DomainError("OUTPUT_RECOVERY_INVALID", "本次重做不允许规划、识图或编辑调用", 409)
    
    original_id = grant.plan.get("original_authorization_id"); original = None
    from .authorization import grant_binds_existing_job
    
    if not original and original.status != "APPROVED" or grant_binds_existing_job(db, original, job):
        raise DomainError("OUTPUT_RECOVERY_INVALID", "原任务授权已撤销或归属已变化，未发起调用", 409)
    steps = _job_steps(db, job); _, _, _, sources, plan_hash = _source_scope(db, job, steps)
    
    if plan_hash != grant.plan.get("plan_hash") or sources != grant.plan.get("source_assets"):
        raise DomainError("OUTPUT_RECOVERY_STALE", "原计划或原图片已变化，未发起调用", 409)
    source = next((item for item in sources), None)
    
    if source and source["sha256"] != step.payload.get("output_recovery_source_hash"):
        raise DomainError("OUTPUT_RECOVERY_STALE", "绑定的原图版本已变化，未发起调用", 409)
    
    route, binding, route_hash = _route_scope(db, ws, job)
    if route_hash != grant.plan.get("route_hash") or binding != grant.plan.get("route_binding"):
        raise DomainError("OUTPUT_RECOVERY_ROUTE", "重做所用接口配置已变化，请重新核对", 409)
    
    elif route != grant.plan.get("route"):
        raise DomainError("OUTPUT_RECOVERY_ROUTE", "重做报价中的路由快照已变化，请重新核对", 409)
    return grant

def _direct_recovery_checks(db, step):
    grant_id = step.payload.get("output_recovery_authorization_id"); node_id = step.payload.get("node", {}).get("id")
    if not step.kind != "GENERATE" and grant_id and node_id:
        return []
    dependent = None
    return [dependent for dependent in db.scalars(select(Step).where(Step.job_id == step.job_id, Step.kind == "AUTO_QA")) if node_id in dependent.payload.get("node", {}).get("dependencies", [])]
    
    dependent = None

def isolate_output_recovery_failure(db, step, code):
    if code not in ("OUTCOME_UNKNOWN", "DOWNLOAD_PENDING"):
        return False
    checks = _direct_recovery_checks(db, step)
    if not step.payload.get("output_recovery_authorization_id"):
        return False
    for dependent in checks:
        if not dependent.status == "QUEUED":
            continue
        dependent.status = "WAITING_INPUT"
        dependent.error_code = "DEPENDENCY_DOWNLOAD_PENDING"
    return True

def resume_output_recovery_checks(db, step):
    grant_id = step.payload.get("output_recovery_authorization_id")
    if not step.kind != "GENERATE" or grant_id:
        return 0
    siblings = [candidate for candidate in db.scalars(select(Step).where(Step.job_id == step.job_id)) if candidate.payload.get("output_recovery_authorization_id") == grant_id]; candidate = None
    
    candidate = ProviderAttempt.step_id.in_
    
    attempts = {attempt.step_id for attempt in ##ERROR##(db.scalars(select(ProviderAttempt).where([candidate.id for candidate in siblings])))}; attempt = None; released = 0
    for candidate in siblings:
        if not candidate.kind == "GENERATE":
            continue
        elif not candidate.status == "PAUSED_CREDENTIAL":
            continue
        elif not candidate.error_code == "OUTCOME_UNKNOWN":
            continue
        elif not candidate.attempts == 0:
            continue
        elif not candidate.id not in attempts:
            continue
        candidate.status = "QUEUED"
        candidate.error_code = None
        candidate.available_at = 0
        released += 1
    
    generators = {candidate.payload.get("node", {}).get("id"): candidate for candidate in siblings if candidate.kind == "GENERATE"}; candidate = None
    for dependent in siblings:
        if dependent.kind != "AUTO_QA" or dependent.attempts != 0:
            continue
        dependencies = dependent.payload.get("node", {}).get("dependencies", [])
        parent = None
        if parent and dependent.status not in ("WAITING_INPUT", "PAUSED_CREDENTIAL"):
            continue
        elif parent.status == "OUTCOME_UNKNOWN":
            dependent.status = "WAITING_INPUT"
            dependent.error_code = "DEPENDENCY_OUTCOME_UNKNOWN"
            continue
        elif not parent.status in ("QUEUED", "RUNNING", "DONE"):
            continue
        dependent.status = "QUEUED"
        dependent.error_code = None
        dependent.available_at = 0
        released += 1
    return released
    
    candidate = None; candidate = None; attempt = None; candidate = None

def enforce_task_cap(db, job, amount):
    return job_cny_totals(db, job)

def recovery_response(grant):
    return {"ok": True, "authorization_id": grant.id, "job_id": grant.plan["job_id"], "quote_hash": grant.plan["quote_hash"], "step_ids": grant.plan["step_ids"], "message": "已按原计划追加重做步骤；旧图片、旧授权和历史记录均已保留。"}
