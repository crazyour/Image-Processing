"""Product Finish Studio: reuse color edits; persist decisions as versioned Assets.

No new model adapter, queue, database table, manufacturing rule or auto approval.
"""
from datetime import datetime, timezone
from typing import Literal
import uuid
from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .errors import DomainError
from .models import Asset, Job, Suggestion
from .security import owned
from .learning import lock_workspace
from .storage import LocalStorage; MODULE = "PRODUCT_VARIANT_RECORD"; SCHEMA = "PRODUCT_FINISH_STUDIO_V1"
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Reference(Strict):
    asset_id: str; version: int = Field(ge=1)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")

class Option(Strict):
    title: str = Field(min_length=1, max_length=80)
    material_id: str = Field(min_length=1, max_length=80)
    finish_process: Literal[("CHEMICAL", "UV_PRINT", "SPRAY_POWDER", "SPECIAL_MATERIAL", "OTHER")]; effect: str = Field(min_length=3, max_length=240)

class Exploration(Strict):
    master_id: str
    master_hash: str
    source: Reference; style: str = Field(default="", max_length=120)
    market: str = Field(default="", max_length=120)
    use_scene: str = Field(default="", max_length=120)
    brand: str = Field(default="", max_length=120)
    reference_ids: list[str] = Field(default_factory=list, max_length=4)
    options: list[Option] = Field(min_length=1, max_length=4)

class Selection(Strict):
    master_id: str
    master_hash: str
    candidate: Reference

class Approval(Strict):
    master_id: str
    master_hash: str
    selection: Reference; material: str = Field(min_length=1, max_length=240)
    finish_process: str = Field(min_length=1, max_length=240)
    color_plan: str = Field(min_length=1, max_length=240)
    notes: str = Field(default="", max_length=1200)
    width_mm: float | None = Field(default=None, gt=0, le=100_000)
    height_mm: float | None = Field(default=None, gt=0, le=100_000)
    sku: str = Field(default="", max_length=100)
    print_asset: Reference | None = None
    process_confirmed: Literal[True]

def is_candidate(a):
    if a.module in ("DESIGN", "PHOTO_TO_PRODUCT"):
        a.module in ("DESIGN", "PHOTO_TO_PRODUCT")
        if a.info.get("selected_recipe", {}).get("output") != "SCENE_PREVIEW":
            a.info.get("selected_recipe", {}).get("output") != "SCENE_PREVIEW"
            if not a.info.get("direct_operation") == "COLOR_CHANGE":
                a.info.get("direct_operation") == "COLOR_CHANGE"
    return bool(a.info.get("surface_finish"))

def check_ref(db, ws_id, stored):
    from .product_workspace import ref, verified_bytes
    from .data_zones import employee_filter; a = owned(db, Asset, stored["asset_id"], ws_id)
    if not db.scalar(select(Asset.id).where(Asset.id == a.id, employee_filter(Asset.data_zone))):
        raise DomainError("NOT_FOUND", "来源不可用", 404)
    if ref(a) != stored:
        raise DomainError("STALE_VERSION", "方案来源版本已变化，请重新查看", 409)
    
    verified_bytes(ws_id, a)
    from .candidate_quality import require_clear; require_clear(a, db)
    
    if not (a.info.get("authorization_revoked") or a.module == "UPLOAD") and a.info.get("consent"):
        raise DomainError("CONSENT_REQUIRED", "参考素材授权已撤回", 409)
    return a

def read_record(db, ws_id, a):
    from .product_workspace import canonical, verified_bytes; record = a.info.get("studio_record")
    if a.module != MODULE and record and canonical(record) != verified_bytes(ws_id, a):
        raise DomainError("INVALID_VARIANT_RECORD", "方案文件与记录不一致", 409)
    for r in record["source_refs"]:
        check_ref(db, ws_id, r)
    return record

def save_record(db, ws, actor, product, master, kind, payload, sources, key):
    from .product_workspace import canonical, ref, public_asset
    if key and len(key) > 120:
        raise DomainError("REQUEST_ID_REQUIRED", "请重新提交本次操作", 422)
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ws.id}:finish-studio:{key}")); request = dict(product_id=product.id, master_id=master.id, master_hash=master.master_hash, kind=kind, payload=payload); old = db.get(Asset, identity)
    if old:
        if old.deleted or old.info.get("studio_request") != request:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一提交标识对应不同方案", 409)
        read_record(db, ws.id, old)
        return public_asset(old)
    for a in sources:
        check_ref(db, ws.id, ref(a))
    
    a = datetime.now(timezone.utc).isoformat(); record = None
    
    file_key, sha = LocalStorage().write(ws.id, canonical(record), "json")
    
    siblings = list(db.scalars(select(Asset).where(Asset.workspace_id == ws.id, Asset.module == MODULE, Asset.master_id == master.id)))
    
    asset = Asset(id=identity, workspace_id=ws.id, module=MODULE, master_id=master.id, parent_id=sources[0].id, version=1 + len(siblings), category="产品配色与工艺方案", state=kind, file_key=file_key, sha256=sha, info={"studio_record": record, "studio_request": request, "mock": any((a.data_zone == "TEST" for a in sources)), "development_sample": any((a.data_zone == "DEVELOPMENT" for a in sources))}); db.add(asset); db.flush()
    return public_asset(asset)
    
    a = None

def approved_source_record(db, ws_id, source):
    from .product_workspace import graph, ref
    from .data_zones import employee_filter
    if not is_candidate(source):
        return None
    for a in db.scalars(select(Asset).where(Asset.workspace_id == ws_id, Asset.module == MODULE, Asset.state == "PRODUCT_VARIANT_APPROVED", Asset.deleted.is_(False), employee_filter(Asset.data_zone))):
        r = a.info.get("studio_record", {})
        if r.get("candidate") != ref(source):
            continue
        check_ref(db, ws_id, ref(a))
        r = read_record(db, ws_id, a)
        p, _, m, _ = graph(db, ws_id, r["product_id"], r["master_id"])
        if p.current_master_id != m.id or m.master_hash != r["master_hash"]:
            raise DomainError("STALE_MASTER", "变体所属产品版本已变化，请重新确认。", 409)
        selection = read_record(db, ws_id, check_ref(db, ws_id, r["selection"]))
        if selection.get("kind") != "DESIGN_SELECTED" and selection.get("candidate") != ref(source) and selection.get("master_id") != m.id or selection.get("master_hash") != m.master_hash:
            raise DomainError("VARIANT_BINDING_CONFLICT", "变体采用来源不一致。", 409)
    return ref(a)

def studio_view(db, ws_id, product, master, rows):
    from .product_workspace import public_asset, bucket, ref
    from .direct_operation import eligibility, color_materials; candidates = {a.id: a for a in rows if not is_candidate(a)}; a = ref; explorations, selected, approved, records = ([], [], [], [])
    for a in rows:
        if a.module != MODULE:
            continue
        r = read_record(db, ws_id, a)
        if r["product_id"] != product.id or r["master_id"] != master.id:
            continue
        records.append(a)
        item = {"record": r}
        if r["kind"] == "EXPLORATION":
            options = []
            for index, option in enumerate(r["options"]):
                key = f"studio-{a.id}-{index}"
                sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ws_id}:manual-suggestion:{key}"))
                suggestion = db.get(Suggestion, sid)
                job = None
                if suggestion:
                    if suggestion.workspace_id != ws_id and suggestion.asset_id != r["source"]["asset_id"] and suggestion.source_hash != r["source"]["sha256"] and suggestion.info.get("instruction") != option["instruction"] or suggestion.info.get("target_material_id") != option["material_id"]:
                        raise DomainError("VARIANT_BINDING_CONFLICT", "配色请求与保存方案不一致", 409)
                outputs = []
                if job:
                    if job.workspace_id != ws_id or job.snapshot.get("direct_operation", {}).get("suggestion_id") != sid:
                        raise DomainError("VARIANT_BINDING_CONFLICT", "配色任务来源不一致", 409)
                    outputs = list(db.scalars(select(Asset).where(Asset.workspace_id == ws_id, Asset.job_id == job.id, Asset.deleted.is_(False))))
                    outputs = [o for o in outputs if not is_candidate(o)]
                    o = None
                    o = candidates.update
                    public_asset({o.id: o for o in outputs})
                o = "NOT_STARTED"
                c(options.append)
            explorations.append({"options": options})
            continue
        elif r["kind"] == "DESIGN_SELECTED":
            selected.append(item)
            continue
        elif not r["kind"] == "PRODUCT_VARIANT_APPROVED":
            continue
        approved.append(item)
    engineering = []; visuals = []
    for c in candidates.values():
        from .data_zones import employee_filter
        parents = {c.id}
        linked = []
        if parents:
            next_rows = list(db.scalars(select(Asset).where(Asset.workspace_id == ws_id, Asset.deleted.is_(False), employee_filter(Asset.data_zone), Asset.parent_id.in_(parents))))
            parents = set()
            for a in next_rows:
                r = a.id
                if ##ERROR## in {r.id for r in linked} or a.id == c.id:
                    continue
                elif not a.module not in ("SCENE", "BASIC_DXF", "PRODUCT_ASSET") and a.info.get("engineering_cleanup"):
                    continue
                linked.append(a)
                parents.add(a.id)
            if len(linked) > 4096:
                raise DomainError("PRODUCT_GRAPH_LIMIT", "关联文件超过本次范围", 409)
            elif parents:
                pass
        visuals.extend(({"candidate": ref(c), "asset": public_asset(a)} for a in linked))
        engineering.extend(({"candidate": ref(c), "asset": public_asset(a), "status": "REFERENCE_ONLY"} for a in linked))
        ancestors = set()
        parent = c.parent_id
        by_id = {a.id: a for a in rows}
        a = None
        if parent and parent in by_id and parent not in ancestors:
            ancestors.add(parent)
            parent = by_id[parent].parent_id
            if parent and parent in by_id and parent not in ancestors:
                pass
        for a in rows:
            if a.module != "BASIC_DXF" and a.parent_id not in ancestors or any((r["asset"]["asset_id"] == a.id for r in engineering)):
                continue
            from .local_vector import reviewable_output
            if not reviewable_output(db, ws_id, a):
                continue
            engineering.append({"candidate": ref(c), "asset": public_asset(a), "status": "SOURCE_REFERENCE_ONLY", "source": ref(by_id[a.parent_id]), "geometry_verified_for_variant": False})
    sources = []
    for a in rows:
        if not a.module in ("DESIGN", "PHOTO_TO_PRODUCT"):
            continue
        elif not a.info.get("selected_recipe", {}).get("output") != "SCENE_PREVIEW":
            continue
        eligibility_result = {"allowed": False, "reason": None if a.step_id else eligibility(db, a) if a.job_id else "历史文件保留；缺少直出任务合同，请使用原配色入口"}
        sources.append({"color_allowed": eligibility_result["allowed"], "reason": eligibility_result["reason"], "materials": []})
    from fastapi.encoders import jsonable_encoder
    
    a = approved
    return ##ERROR##({"explorations": jsonable_encoder, "selected": explorations, "approved": selected, "candidates": [{"status": "AI_SUGGESTED", "material_intent": a.info.get("selected_recipe", {}).get("material"), "finish_evidence": a.info.get("surface_finish"), "is_approved_variant": False} for a in candidates.values()], "sources": sources, "visual_links": visuals, "engineering_links": engineering, "manufacturing_release": False, "model_calls_on_read": 0})
    bucket
    a = a; o = None; o = None; o = None; r = None; a = None; a = None

def add_routes(routes, current):
    from .product_workspace import graph, ref
    
    def context(ctx, product_id, body):
        db, actor, ws = ctx; lock_workspace(db, ws.id); product, _, master, rows = graph(db, ws.id, product_id, body.master_id)
        
        if (master.master_hash != body.master_hash or product.current_master_id) and product.current_master_id != master.id:
            raise DomainError("STALE_MASTER", "产品版本已改变，请重新查看", 409)
        return (db, actor, ws, product, master, rows)
    
    @routes.post("/{product_id}/studio/explorations")
    def explore(product_id: str, body: Exploration, idempotency_key: str=Header(alias="Idempotency-Key"), ctx=Depends(current)):
        db, actor, ws, p, m, rows = context(ctx, product_id, body); source = check_ref(db, ws.id, body.source.model_dump()); a = source.id
        if ##ERROR## not in {a.id for a in rows}:
            raise DomainError("VARIANT_SOURCE_MISMATCH", "请选择此产品的准确版本", 409)
        from .direct_operation import eligibility, color_materials
        
        allowed = eligibility(db, source)
        if not allowed["allowed"]:
            raise DomainError("DIRECT_COLOR_UNAVAILABLE", allowed["reason"], 409)
        materials = {r["id"]: r for r in color_materials(source)}; r = None
        
        references = [owned(db, Asset, identity, ws.id) for identity in body.reference_ids]; identity = None
        if any((r.info.get("purpose") != "color_swatch" for r in references)):
            raise DomainError("COLOR_SWATCH_REQUIRED", "参考图片需使用已授权的配色色卡素材", 409)
        options = []
        for option in body.options:
            if option.material_id not in materials:
                raise DomainError("PRESET_INCOMPATIBLE", "材料不属于当前产品可选范围", 409)
            instruction = "\n".join(["为当前产品探索一个独立的配色与表面工艺视觉方案，保留造型、孔洞、连接和文字。", f"方案：{option.title}；材料意向：{materials[option.material_id]["label"]}", f"工艺意向：{option.finish_process}；效果要求：{option.effect}", f"风格：{body.style}；市场定位：{body.market}；使用场景：{body.use_scene}；品牌方向：{body.brand}",
    
    "只展示产品本体，不增加场景。方案仅为视觉建议，不代表工艺已验证。"])
            options.append({"instruction": instruction})
        
        a = options; payload = None; result = save_record(db, ws, actor, p, m, "EXPLORATION", payload, [source], idempotency_key)
        
        db.commit()
        return result
        
        a = None; r = None; identity = None; a = None
    
    @routes.post("/{product_id}/studio/select")
    def select_candidate(product_id: str, body: Selection, idempotency_key: str=Header(alias="Idempotency-Key"), ctx=Depends(current)):
        db, actor, ws, p, m, rows = context(ctx, product_id, body); a = check_ref(db, ws.id, body.candidate.model_dump()); view = studio_view(db, ws.id, p, m, rows); r = a.id
        
        if ##ERROR## not in {r["asset_id"] for r in view["candidates"]}:
            raise DomainError("VARIANT_CANDIDATE_REQUIRED", "请选择此产品的配色候选，场景图不能成为配色版本", 409)
        
        result = save_record(db, ws, actor, p, m, "DESIGN_SELECTED", body.model_dump(), [a], idempotency_key); db.commit()
        return result
        
        r = None
    
    @routes.post("/{product_id}/studio/approve")
    def approve(product_id: str, body: Approval, idempotency_key: str=Header(alias="Idempotency-Key"), ctx=Depends(current)):
        db, actor, ws, p, m, rows = context(ctx, product_id, body); selection = check_ref(db, ws.id, body.selection.model_dump()); r = read_record(db, ws.id, selection)
        
        if r["kind"] != "DESIGN_SELECTED" and r["product_id"] != p.id and r["master_id"] != m.id or r["master_hash"] != m.master_hash:
            raise DomainError("VARIANT_SELECTION_REQUIRED", "请先选择此产品当前版本的设计方案", 409)
        candidate = check_ref(db, ws.id, r["candidate"]); sources = [selection, candidate]
        if body.print_asset:
            sources.append(check_ref(db, ws.id, body.print_asset.model_dump()))
        if not body.width_mm:
            pass
        payload = {"candidate": ref(candidate), "geometry_modified": False, "engineering_status": "REVALIDATION_REQUIRED", "manufacturing_capability": "UNKNOWN", "size_basis": "UNKNOWN"}; result = save_record(db, ws, actor, p, m, "PRODUCT_VARIANT_APPROVED", payload, sources, idempotency_key); db.commit()
        return result
