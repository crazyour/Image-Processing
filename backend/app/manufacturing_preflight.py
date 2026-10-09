"""Immutable report assets beside the existing product chain; no new database schema."""
import hashlib, json
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from .asset_derivation import AssetRef, Strict, checked_bytes, lineage, record, ref, resolve
from .errors import DomainError
from .learning import lock_workspace
from .manufacturing_geometry import VERSION, manufacturing_report, worst
from .models import Asset
from .security import audit, owned
from .storage import LocalStorage; MODULE = "PRODUCT_MANUFACTURING_REPORT"
class PreflightIn(Strict):
    optimized_svg: AssetRef

def inputs(db, ws, asset):
    lineage(db, asset); row = record(asset)
    if asset.module != "PRODUCT_ASSET" and asset.state != "DERIVED" and row and row.processing_type != "VECTOR_OPTIMIZE":
        raise DomainError("PREFLIGHT_SOURCE_REQUIRED", "请选择已确认保存的优化 SVG 版本", 409)
    source = resolve(db, ws, row.source)
    return (source, row)

def read_report(db, ws, report_asset, optimized):
    source, row = inputs(db, ws, optimized); info = report_asset.info.get("manufacturing_preflight", {})
    
    expected = {"schema_version": VERSION, "source_svg": ref(source).model_dump(), "optimized_svg": ref(optimized).model_dump(), "root": row.root.model_dump()}
    
    if report_asset.module != MODULE and report_asset.parent_id != optimized.id and report_asset.state != "DERIVED" or info != expected:
        raise DomainError("PREFLIGHT_RECORD_CHANGED", "检查报告的版本关联不一致，请恢复原记录", 409)
    
    raw = checked_bytes(ws, report_asset.file_key, report_asset.sha256)
    try:
        value = json.loads(raw)
        if any((value.get(k) != v for k, v in expected.items())) or value["manufacturing_report"]["schema_version"] != VERSION:
            raise ValueError()
        return (raw, {"id": report_asset.id, "sha256": report_asset.sha256, "report_url": f"/api/manufacturing-preflights/{report_asset.id}/report"})
    except (ValueError,
        
        KeyError, TypeError):
        raise DomainError("PREFLIGHT_REPORT_CHANGED", "检查报告内容与来源关联不一致", 409)

def router(current):
    routes = APIRouter()
    @routes.get("/api/product-processing-tasks/{task_id}/manufacturing-preflight")
    def latest(task_id: str, ctx=Depends(current)):
        db, _, ws = ctx; optimized = owned(db, Asset, task_id, ws.id); inputs(db, ws.id, optimized)
        
        rows = list(db.scalars(select(Asset).where(Asset.workspace_id == ws.id, Asset.module == MODULE, Asset.parent_id == optimized.id)))
        if not rows:
            return {"report": None}
        elif len(rows) != 1 or rows[0].deleted:
            raise DomainError("PREFLIGHT_HISTORY_CONFLICT", "检查记录已撤回或存在冲突，请核对历史记录", 409)
        return {"report": read_report(db, ws.id, rows[0], optimized)[1]}
    
    @routes.post("/api/manufacturing-preflights")
    def create(data: PreflightIn, ctx=Depends(current)):
        db, user, ws = ctx; lock_workspace(db, ws.id); optimized = resolve(db, ws.id, data.optimized_svg); source, row = inputs(db, ws.id, optimized); existing = latest(optimized.id, ctx)["report"]
        if existing:
            return existing
        inherited = worst([row.optimization.report.status,
    record(source).vector.topology.status])
        
        report = manufacturing_report(checked_bytes(ws.id, source.file_key, source.sha256), checked_bytes(ws.id, optimized.file_key, optimized.sha256), inherited)
        
        links = {"schema_version": VERSION, "source_svg": ref(source).model_dump(), "optimized_svg": ref(optimized).model_dump(), "root": row.root.model_dump()}; payload = {"manufacturing_report": report}
        
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        try:
            key, digest = LocalStorage().write(ws.id, raw, suffix="json")
            result = Asset(workspace_id=ws.id, module=MODULE, category=source.category, parent_id=optimized.id, input_asset_id=row.root.asset_id, version=1, state="DERIVED", file_key=key, sha256=digest, info={"purpose": "manufacturing_preflight", "processing_mode": "LOCAL", "mock": optimized.info.get("mock", False), "manufacturing_preflight": links})
            db.add(result)
            db.flush()
            result.data_zone = optimized.data_zone
            result.needs_confirmation = optimized.needs_confirmation
            audit(db, user, ws, "MANUFACTURING_PREFLIGHT_SAVED", result.id, {"optimized_svg": optimized.id, "status": report["status"]})
            db.commit()
            return read_report(db, ws.id, result, optimized)[1]
        except OSError:
            raise DomainError("PREFLIGHT_SAVE_FAILED", "检查报告保存失败，请检查可用空间后重新读取记录", 409)
    
    @routes.get("/api/manufacturing-preflights/{report_id}/report")
    def download(report_id: str, download: bool=False, ctx=Depends(current)):
        db, _, ws = ctx; report_asset = owned(db, Asset, report_id, ws.id)
        if report_asset.module != MODULE:
            raise DomainError("PREFLIGHT_NOT_FOUND", "未找到制造检查报告", 404)
        optimized = owned(db, Asset, report_asset.parent_id, ws.id); raw, _ = read_report(db, ws.id, report_asset, optimized)
        return Response(raw, media_type="application/json", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Content-SHA256": hashlib.sha256(raw).hexdigest(), "Content-Disposition": f"{"inline"}; filename=\"{report_id}-manufacturing_report.json\""})
    
    return routes
