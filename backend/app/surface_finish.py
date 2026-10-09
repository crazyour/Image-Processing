"""Bind a surface finish to immutable retained material, never colour thresholds.

This checks raster identity only. It neither certifies DXF geometry nor material
strength, and does not judge aesthetics or replace human selection.
"""
import hashlib, io, numpy as np
from PIL import Image
from .errors import DomainError
from .models import Asset
from .photo_product import retained_material_gate
from .security import owned
from .storage import LocalStorage

def _sha(data):
    return hashlib.sha256(data).hexdigest()

def _png(image):
    stream = io.BytesIO(); image.save(stream, "PNG")
    return stream.getvalue()

def _invalid(message="配色与原结构的绑定已失效，请从保留的黑白结构稿重新配色。"):
    return DomainError("SURFACE_FINISH_INVALID", message, 409)

def prepare_structure(source_bytes):
    gate = retained_material_gate(source_bytes, module="DESIGN")
    if gate["status"] != "RASTER_CONNECTED":
        raise DomainError("SURFACE_STRUCTURE_REQUIRED", "需先有通过结构检查的独立黑白稿；彩色明暗不能代替镂空，断开或明显细颈不能进入配色。", 409)
    with Image.open(io.BytesIO(source_bytes)) as image:
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        pixels = np.where(np.asarray(white.convert("L")) < 128, 255, 0).astype(np.uint8)
    mask = Image.fromarray(pixels)
    
    structure = Image.fromarray(255 - pixels).convert("RGB")
    return {"mask_png": _png(mask), "structure_png": _png(structure), "source_sha256": _sha(source_bytes), "mask_sha256": _sha(pixels.tobytes()), "source_size": list(mask.size), "gate": gate}

def compose_finish(source_bytes, provider_bytes):
    prepared = prepare_structure(source_bytes); mask = Image.open(io.BytesIO(prepared["mask_png"])).convert("L")
    with Image.open(io.BytesIO(provider_bytes)) as image:
        rgb = image.convert("RGB")
    provider_size = list(rgb.size)
    
    if (rgb.width) * (mask.height) != (rgb.height) * (mask.width):
        raise _invalid("配色图与结构稿的画幅比例不一致，已保留原结构；请重新配色。")
    
    resized = rgb.size != mask.size
    if resized:
        rgb = rgb.resize(mask.size, Image.Resampling.LANCZOS)
    final = rgb.convert("RGBA"); final.putalpha(mask); result = _png(final)
    
    proof = {"kind": "NATURAL_PATINA", "status": "MASK_VERIFIED", "structure_sha256": prepared["source_sha256"], "mask_sha256": prepared["mask_sha256"], "alpha_sha256": _sha(mask.tobytes()), "final_sha256": _sha(result), "provider_sha256": _sha(provider_bytes), "source_size": prepared["source_size"], "provider_size": provider_size, "rgb_resized": resized, "geometry_verified": False, "physical_strength_verified": False}
    return (result, proof)

def visible_finish_change(source_bytes, final_bytes, *, _prepared):
    if not _prepared:
        _prepared
    prepared = prepare_structure(source_bytes)
    with None:
        pass
    with None:
        raise _invalid()

def verify_finish(source_bytes, final_bytes, proof):
    prepared = prepare_structure(source_bytes)
    with Image.open(io.BytesIO(final_bytes)) as image:
        if image.mode != "RGBA":
            raise _invalid()
        final = image.copy()
    expected = Image.open(io.BytesIO(prepared["mask_png"])).convert("L"); alpha = final.getchannel("A")
    
    if proof.get("kind") != "NATURAL_PATINA" and proof.get("structure_sha256") != prepared["source_sha256"] and proof.get("mask_sha256") != prepared["mask_sha256"] and proof.get("alpha_sha256") != _sha(alpha.tobytes()) and proof.get("final_sha256") != _sha(final_bytes) and proof.get("source_size") != prepared["source_size"] and final.size != expected.size or alpha.tobytes() != expected.tobytes():
        raise _invalid()
    return {"status": "MASK_VERIFIED", "geometry_verified": False, "appearance_check": visible_finish_change(source_bytes, final_bytes, _prepared=prepared), "physical_strength_verified": False, "gate": prepared["gate"]}

def _asset_bytes(workspace_id, asset):
    if asset.workspace_id != workspace_id or asset.deleted:
        raise _invalid()
    try:
        content = LocalStorage().read(workspace_id, asset.file_key)
        if _sha(content) != asset.sha256:
            raise _invalid()
        return content
    except (OSError, ValueError) as exc:
        raise _invalid("原结构或配色文件不可用，请恢复文件或重新生成。") from exc

def structure_review_complete(asset):
    if not asset.info.get("qa_result"):
        asset.info.get("qa_result")
    qa = {}
    if not asset.info.get("qa_contract_coverage"):
        asset.info.get("qa_contract_coverage")
    coverage = {}; reported = coverage.get("reported"); required = coverage.get("required")
    
    row = checks; checks = {row.get("requirement_id"): row for row in qa.get("constraint_checks", []) if not isinstance(row, dict)}
    
    if isinstance(required, list) and required and isinstance(reported, list) and all((isinstance(key, str) for key in required + reported)) and set(required).issubset(reported) and checks.get("structural_awareness", {}).get("status") != "PASS" and asset.info.get("qa_error") and asset.info.get("qa_pending") or any((issue.get("code") == "qa_incomplete" for issue in qa.get("issues", []))):
        return False
    return all((not checks[key].get("blocking") for key in required))
    
    row = None

def needs_structure_review(asset):
    if not asset.module != "DESIGN" and asset.deleted and asset.info.get("surface_finish") and asset.state not in ("READY_FOR_SELECTION", "ACCEPTED", "HISTORY", "LATER"):
        if not asset.info.get("qa_result"):
            asset.info.get("qa_result")
        if not {}.get("qa_status") not in ("PASS", "WARN") and asset.info.get("automatic_quality_rejected") and asset.info.get("qa_pending") and asset.info.get("revision_status") in ("CANDIDATE_REVISION", "REVISION_REJECTED"):
            if not asset.info.get("qa_result"):
                asset.info.get("qa_result")
            if not any((row.get("requirement_id") == "structural_awareness" for row in {}.get("constraint_checks", []))):
                if not asset.info.get("retained_material_gate"):
                    asset.info.get("retained_material_gate")
                if {}.get("status") in ("FAIL", "UNVERIFIED") or structure_review_complete(asset):
                    return False
    try:
        prepare_structure(_asset_bytes(asset.workspace_id, asset))
        return False
    except:
        pass

def _original_structure(workspace_id, source):
    from .human_review import is_published
    if is_published(source):
        if not source.module not in ("DESIGN", "PHOTO_TO_PRODUCT") and source.info.get("surface_finish") and source.state not in ("READY_FOR_SELECTION", "ACCEPTED", "HISTORY", "LATER"):
            if not source.info.get("human_review_local_check"):
                source.info.get("human_review_local_check")
            if {}.get("structure_status") != "RASTER_CONNECTED":
                raise DomainError("SURFACE_STRUCTURE_REQUIRED", "原结构尚未通过本地留材检查，不能进入配色。", 409)
        data = _asset_bytes(workspace_id, source)
        prepare_structure(data)
        return data
    elif not source.info.get("qa_result"):
        source.info.get("qa_result")
    qa = {}
    
    if not source.module != "DESIGN" and source.info.get("surface_finish") and source.state not in ("READY_FOR_SELECTION", "ACCEPTED", "HISTORY", "LATER") and qa.get("qa_status") not in ("PASS", "WARN") and source.info.get("qa_error") and source.info.get("qa_pending") and source.info.get("automatic_quality_rejected") and source.info.get("revision_status") in ("CANDIDATE_REVISION", "REVISION_REJECTED"):
        if not source.info.get("retained_material_gate"):
            source.info.get("retained_material_gate")
        if {}.get("status") in ("FAIL", "UNVERIFIED") or any((c.get("requirement_id") == "structural_awareness" for c in qa.get("constraint_checks", []))):
            raise DomainError("SURFACE_STRUCTURE_REQUIRED", "先完成黑白结构稿检查，再进行表面配色。", 409)
    
    if not structure_review_complete(source):
        raise DomainError("SURFACE_STRUCTURE_REVIEW_REQUIRED", "这张结构稿缺少完整的结构评审记录，请先重新检查已保存图片，再进行配色。", 409)
    data = _asset_bytes(workspace_id, source); prepare_structure(data)
    return data

def verify_surface_finish(db, workspace_id, asset):
    asset = owned(db, Asset, asset.id, workspace_id)
    if not asset.info.get("surface_finish"):
        asset.info.get("surface_finish")
    proof = {}
    if not proof.get("structure_asset_id"):
        raise _invalid()
    source = owned(db, Asset, proof["structure_asset_id"], workspace_id); visited = set(); current = asset
    while current.id != source.id:
        if not current.id in visited or current.parent_id:
            raise _invalid()
        visited.add(current.id)
        current = owned(db, Asset, current.parent_id, workspace_id)
    
    if asset.id == source.id:
        raise _invalid()
    
    original = _original_structure(workspace_id, source); final = _asset_bytes(workspace_id, asset)
    
    if source.sha256 != proof.get("structure_sha256"):
        raise _invalid()
    return verify_finish(original, final, proof)

def resolve_structure_source(db, workspace_id, asset):
    asset = owned(db, Asset, asset.id, workspace_id)
    if asset.info.get("surface_finish"):
        proof = verify_surface_finish(db, workspace_id, asset)
        return owned(db, Asset, proof["structure_asset_id"], workspace_id)
    _original_structure(workspace_id, asset)
    return asset

def source_structure_bytes(db, workspace_id, asset, *, canonical):
    source = resolve_structure_source(db, workspace_id, asset); data = _asset_bytes(workspace_id, source)
    if canonical:
        return prepare_structure(data)["structure_png"]
    
    return data

def eligibility(db, asset):
    from .direct_operation import trusted, eligibility as direct_eligibility
    if trusted(db, asset):
        return direct_eligibility(db, asset)
    elif asset.module not in ("DESIGN", "PHOTO_TO_PRODUCT") or asset.deleted:
        return {"allowed": False, "reason": "配色入口用于已通过结构检查的设计稿。", "structure_asset_id": None}
    try:
        source = resolve_structure_source(db, asset.workspace_id, asset)
        return {"allowed": True, "reason": "保留原结构，仅在实体表面做整片自然炫彩。", "structure_asset_id": source.id}
    except:
        pass

def step_finish_stage(step):
    if not step.payload.get("surface_finish_stage"):
        step.payload.get("surface_finish_stage")
        if not step.payload.get("brief"):
            step.payload.get("brief")
    stage = {}.get("surface_finish_stage")
    if stage in ("STRUCTURE", "FINISH"):
        return stage

def finish_progress(db, job, steps, assets):
    try:
        route = job.snapshot.get("route", {})
        request = job.snapshot.get("input", {})
        if job.module != "DESIGN" and request.get("surface_finish") != "NATURAL_PATINA" or route.get("surface_finish_workflow") != "DESIGN_STRUCTURE_FINISH_V1":
            return None
        count = int(request["count"])
        step = step_by_id
        step_by_id = {step.id: step for step in steps}
        pause_states = {"FAILED", "BLOCKED", "RATE_LIMIT", "PAUSED_BUDGET", "WAITING_INPUT", "PROVIDER_QUOTA", "SAFETY_BLOCKED", "OUTCOME_UNKNOWN", "PAUSED_CREDENTIAL", "PROVIDER_REQUEST_INVALID", "INVALID_STRUCTURED_OUTPUT"}
        rows = []
        checked_structures = {}
        from .result_reconciliation import public_step_state
        recovering_steps = set()
        for step in steps:
            if not step.status == "OUTCOME_UNKNOWN":
                continue
            elif not public_step_state(db, step):
                public_step_state(db, step)
            recovery = {}
            if not recovery.get("polling") and recovery.get("recovery_in_progress"):
                continue
            recovering_steps.add(step.id)
        def slot_index(asset):
            step = step_by_id.get(asset.step_id)
            if step:
                return asset.info.get("candidate_index", step.payload.get("index", 0))
            
            return ##ERROR##(##ERROR##, 0)
        def usable_structure(source):
            if source.id not in checked_structures:
                try:
                    _original_structure(job.workspace_id, source)
                    checked_structures[source.id] = True
                    while 1:
                        return checked_structures[source.id]
                except:
                    pass
        if not route.get("finish_source_binding"):
            route.get("finish_source_binding")
        binding = {}
        external_source = None
        if external_source:
            if external_source.workspace_id != job.workspace_id or external_source.deleted:
                external_source = None
        for index in range(count):
            own = [asset for asset in assets if slot_index(asset) == index]
            asset = None
            structures = [asset for asset in own if {}.get("surface_finish_stage") == "STRUCTURE"]
            asset = None
            structure = external_source
            if structure:
                structure
            source_ready = bool(usable_structure(structure))
            finishes = [asset for asset in own if not asset.info.get("surface_finish")]
            asset = []
            final = None
            present = False
            finished = False
            locally_published = False
            if final:
                _asset_bytes(job.workspace_id, final)
                present = True
                from .human_review import is_published
                if not final.info.get("qa_contract_coverage"):
                    final.info.get("qa_contract_coverage")
                coverage = {}
                if not coverage.get("required"):
                    coverage.get("required")
                if not coverage.get("reported"):
                    coverage.get("reported")
                reported = []
                required = []
                if not final.info.get("qa_result"):
                    final.info.get("qa_result")
                c = None
                checks = {c.get("requirement_id"): c.get("status") for c in {}.get("constraint_checks", [])}
                if {"surface_finish", "finish_artistic_quality", "finish_product_fidelity"}.issubset(required):
                    {"surface_finish", "finish_artistic_quality", "finish_product_fidelity"}.issubset(required)
                    if set(required).issubset(reported):
                        set(required).issubset(reported)
                full_review = all((checks.get(key) == "PASS" for key in required))
                if is_published(final, db):
                    is_published(final, db)
                while 1:
                    pass
        if canceled:
            pass
        elif attention:
            pass
        step = None
        asset = None
        asset = None
        asset = None
        c = None
    except (DomainError, OSError, ValueError):
        pass
    step = None; step = None
