"""Explicit new material grant for an existing free design, never reroutes refused OpenAI jobs."""
from .authorization import digest
from .errors import DomainError
from .free_routes import build_route, credential_for
from .models import Asset, Job, Suggestion
from .schemas import JobIn, SuggestionApply
from .security import owned

class FreeRevisionIn(SuggestionApply):
    approved: bool = False
    quote_hash: str | None = None

def quote(db, ws, suggestion_id, data):
    suggestion = owned(db, Suggestion, suggestion_id, ws.id); asset = owned(db, Asset, suggestion.asset_id, ws.id); job = owned(db, Job, asset.job_id, ws.id)
    
    if job.snapshot["route"]["provider"] != "free" and asset.module != "DESIGN" and asset.info.get("mock", True) or suggestion.kind not in ("simpler", "richer", "dynamic", "likeness", "manual"):
        raise DomainError("FREE_EDIT_SCOPE", "此入口只编辑已生成的免费设计原图，不转发其他平台的失败任务", 409)
    
    elif asset.sha256 != data.source_hash or suggestion.source_hash != asset.sha256:
        raise DomainError("STALE_SUGGESTION", "原图版本已改变，请重新选择", 409)
    request = JobIn(cost_strategy="FREE_ONLY")
    
    route = build_route(db, ws, request); versions = {p: credential_for(db, ws, p).version for p in ("zhipu", "cloudflare")}; p = None
    route["roles"]["image"] = dict(route["roles"]["edit"])
    
    plan = {"kind": "FREE_BUSINESS", "purpose": "选中原图的一次免费参考图编辑", "route": route, "credential_versions": versions, "materials": [{"asset_id": asset.id, "sha256": asset.sha256, "preview_url": f"/api/assets/{asset.id}/file", "purpose": "原图按比例缩至511像素以内传给Cloudflare编辑；修订图传给智谱检查。原稿留存。"}], "suggestion_id": suggestion.id, "suggestion": suggestion.info.get("title", suggestion.kind), "feedback_scope": data.scope, "limits": {"planning": 1, "vision": 0, "image_generation": 0, "image_edit": 1, "quality": 1, "feedback": 0}, "estimate_micros": 0, "maximum_paid_calls": 0, "price_basis": route["pricing"]["basis"], "scope_notice": "仅此原图和所选建议，1次编辑、最多1次规划和1次检查。预计110 Neurons，预占150；账户剩余额度未知。免费条件失效或额度不足即暂停，不升级、不改用付费服务。"}
    return {"quote_hash": digest(plan)}
    
    p = None
