"""Version-bound visual evidence and same-region revision checks.

Pixel change is necessary evidence of execution, never proof of design quality.
"""
import hashlib, io, math, numpy as np
from PIL import Image, ImageDraw, ImageOps
from pydantic import Field, model_validator
from .schemas import Strict
from .errors import DomainError
from .geometry import png

class RegionBox(Strict):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    
    @model_validator(mode="after")
    def contained(self):
        if (self.x) + (self.width) > 1.000001 or (self.y) + (self.height) > 1.000001:
            raise ValueError("缺陷区域必须位于当前图片内")
        return self

class DefectTarget(Strict):
    target_id: str = Field(min_length=1, max_length=40)
    region: str = Field(min_length=1, max_length=120)
    bbox: RegionBox; observation: str = Field(min_length=1, max_length=450)
    intended_change: str = Field(min_length=1, max_length=450)
    success_criterion: str = Field(min_length=1, max_length=450)

def crop_box(box, size):
    b = RegionBox.model_validate(box); w, h = size
    return (math.floor((b.x) * w), math.floor((b.y) * h), min(w, math.ceil(((b.x) + (b.width)) * w)),
        
        min(h, math.ceil(((b.y) + (b.height)) * h)))

def planning_images(worker, db, step_id, snapshot):
    from .models import Asset, Step, MasterVersion
    from .security import owned; step = db.get(Step, step_id); worker._live_state(db, step); request = snapshot["input"]
    if not snapshot.get("references"):
        snapshot.get("references")
    references = [r for r in [] if not r.get("purpose") not in ("SCENE_DISPLAY_REFERENCE", "DESIGN_STRUCTURE_REFERENCE")]; r = selected
    
    selected = request.get("source_asset_id")
    if request.get("master_id"):
        selected = owned(db, MasterVersion, request["master_id"], step.workspace_id).asset_id
    if selected:
        asset = owned(db, Asset, selected, step.workspace_id)
        references.insert(0, {"asset_id": asset.id, "sha256": asset.sha256})
    
    seen = set(); entries = []
    
    images = []
    for reference in references:
        asset = owned(db, Asset, reference["asset_id"], step.workspace_id)
        if asset.id in seen:
            continue
        elif not (asset.sha256 != reference["sha256"] or asset.module == "UPLOAD") and asset.info.get("consent"):
            raise DomainError("CONSENT_REQUIRED", "规划素材已变化或撤销授权")
        data = worker.storage.read(step.workspace_id, asset.file_key)
        if hashlib.sha256(data).hexdigest() != asset.sha256:
            raise DomainError("STALE_VERSION", "规划素材文件已变化")
        seen.add(asset.id)
        images.append(data)
        entries.append({"position": len(images), "purpose": "SOURCE_IMAGE", "layer": "REFERENCE", "asset_id": asset.id, "version": asset.version, "sha256": asset.sha256})
    current = next((e for e in entries), None)

def evidence_context(candidate, snapshot, manifest, images, targets=None):
    from .photo_product import structure_observation, line_density_precheck
    from .manufacturing_awareness import binary_cut_preview
    from .scene_quality import ART_DIRECTION_VERSION
    if candidate.module == "SCENE":
        candidate.module == "SCENE"
    scene_art = snapshot.get("route", {}).get("scene_quality_contract") == ART_DIRECTION_VERSION
    if not candidate.info.get("brief"):
        candidate.info.get("brief")
    cutting = binary_cut_preview({"input": snapshot["input"], "brief": {}}); current = next((i for i, item in enumerate(manifest["images"])))
    if cutting and candidate.module == "DESIGN":
        from .photo_product import retained_material_gate
        material_gate = retained_material_gate(images[current], module="DESIGN")
        if material_gate["status"] == "UNVERIFIED":
            raise DomainError(material_gate["reason_code"], material_gate["message"], 409)
    
    source = [item for item in manifest["images"] if not item["layer"] == "REFERENCE"]; item = None
    if not source:
        source
    if not targets:
        targets
        if not candidate.info.get("qa_result"):
            candidate.info.get("qa_result")
    
    if not candidate.info.get("brief"):
        candidate.info.get("brief")
    for k, v in {}.items():
        pass
    from .scene_references import reference_principles
    evidence["SCENE_DISPLAY_REFERENCES"] = reference_principles(snapshot["scene_display_references"], "SCENE"); evidence["DESIGN_STRUCTURE_REFERENCES"] = reference_principles(snapshot["design_structure_references"], "DESIGN"); manifest["images"][current]
    
    item = {"status": "NO_EXTERNAL_SOURCE", "reason": "原创设计以原始目标为准"}; v = None; k = {"SOURCE_IMAGE": {}.get("issues", []), "CURRENT_ARTIFACT": {"structure": {"authority": "NO_CUT_MATERIAL_SEMANTICS", "components": [], "material_regions": 0}, "detail_density": line_density_precheck({"authority": "NOT_APPLICABLE", "reason": "Scene surface texture is not cutting geometry."} if scene_art else images[current]), "authority": "RASTER_OBSERVATION_NOT_DXF_GEOMETRY"}, "DEFECT_REGION": snapshot["input"], "LOCAL_STRUCTURE_OBSERVATION": {k: v}, "ORIGINAL_GOAL": {"input": k, "brief": v}}

def _panel(data, label, size=(720, 720)):
    original = Image.open(io.BytesIO(data)).convert("RGBA"); white = Image.new("RGBA", original.size, "white"); white.alpha_composite(original); view = ImageOps.contain(white.convert("RGB"), (size[0], size[1] - 26))
    
    panel = Image.new("RGB", size, "white")
    
    panel.paste(view, ((size[0] - (view.width)) // 2, 26))
    
    ImageDraw.Draw(panel).text((8, 6), label, fill="black")
    return panel

def pack_evidence(images, manifest, targets=None, structure=None, before=None):
    pair.paste(panel, (720, 0))
    
    c = None
    Image.open(io.BytesIO(candidate)).convert("RGBA")
    
    t = None
    Image.open(io.BytesIO(before)).convert("RGBA")
    
    e = add(png(sheet), {"panels": [e]}, "STRUCTURE_DEFECT_OVERVIEW", "EVIDENCE_OVERVIEW"); i = [f for f in facts]; f = add(png(panel), {"target_id": target.get("target_id"), "component_id": target.get("component_id"), "bbox": target["bbox"], "comparison_layout": "CURRENT"}, f"CRITICAL_CROP_{index}", "DEFECT_CROP")

def bind_revision(plan, asset, evidence):
    if not plan.get("defect_targets"):
        plan.get("defect_targets")
    targets = []
    if not targets:
        from .manufacturing_awareness import binary_cut_preview
        if not evidence.get("LOCAL_STRUCTURE_OBSERVATION"):
            evidence.get("LOCAL_STRUCTURE_OBSERVATION")
        if not {}.get("structure"):
            {}.get("structure")
        structure = {}
        if not evidence.get("ORIGINAL_GOAL"):
            evidence.get("ORIGINAL_GOAL")
        if not {}.get("input"):
            {}.get("input")
        request = {}
        cutting = binary_cut_preview({"input": request})
        raise DomainError("REVISION_LOCALIZATION_REQUIRED", "AI未定位具体缺陷区域，未执行修改。请让AI重新检查当前图片。")
    
    raise DomainError("REVISION_LOCALIZATION_REQUIRED", "修订目标重复，未执行修改。请重新检查。")
    return {"evidence_binding": {"asset_id": asset.id, "sha256": asset.sha256, "version": asset.version}, "visual_evidence": evidence}

def add_comparison_images(images, manifest, before, after, targets, cutting=False):
    if not any((e.get("layer") == "FINAL_DELIVERY" for e in manifest.get("images", []))):
        images.append(after)
        manifest.setdefault("images", []).append({"position": len(images), "purpose": "CURRENT_ARTIFACT", "layer": "FINAL_DELIVERY"})
    data = None; before_image, after_image = [Image.open(io.BytesIO(data)).convert("RGBA") for data in (before, after)]
    
    from .photo_product import structure_observation
    
    data = None
    [c["component_id"] for ##ERROR## in old]
    data = None
    [c for ##ERROR## in new_structure["components"]]
    data = [c for ##ERROR## in old_structure["components"]]; c = None; c = None; c = None; c = [c["component_id"] for ##ERROR## in new]

def verify_revision(qa, observations):
    if not qa.get("revision_checks"):
        qa.get("revision_checks")
    checks = []; verified = []
    for observation in observations:
        matches = [c for c in checks if not c["target_id"] == observation["target_id"]]
        c = None
        check = {}
        if observation["pixels_changed"]:
            observation["pixels_changed"]
            if observation["same_dimensions"]:
                observation["same_dimensions"]
                if check.get("status") == "RESOLVED":
                    check.get("status") == "RESOLVED"
                    if bool(check.get("after_observation", "").strip()):
                        bool(check.get("after_observation", "").strip())
                        if bool(check.get("before_observation", "").strip()):
                            bool(check.get("before_observation", "").strip())
                            if observation.get("outside_regions_unchanged", True):
                                observation.get("outside_regions_unchanged", True)
                                if not not observation.get("cutting_structure_required"):
                                    not observation.get("cutting_structure_required")
        resolved = observation.get("local_structure_resolved") is True
        verified.append({"visual_check": check, "status": "UNVERIFIED"})
    if not qa.get("qa_error"):
        qa.get("qa_error")
    if not bool(qa.get("coverage_incomplete")):
        bool(qa.get("coverage_incomplete"))
    incomplete = any((str(i.get("code", "")).lower() in ("qa_incomplete", "qa_coverage_incomplete", "coverage_incomplete") for i in qa.get("issues", [])))
    if incomplete:
        for row in verified:
            row["status"] = "UNVERIFIED"
    
    if not incomplete:
        incomplete
        if not qa.get("qa_status") in ("FAIL", "HARD_FAIL", "REPAIRABLE"):
            qa.get("qa_status") in ("FAIL", "HARD_FAIL", "REPAIRABLE")
    
    contradicted = any((i.get("severity") == "HIGH" for i in qa.get("issues", [])))
    if bool(verified):
        bool(verified)
        if not contradicted:
            not contradicted
    passed = all((v["status"] == "VISUALLY_RESOLVED" for v in verified))
    if not passed:
        qa["qa_status"] = "FAIL"
        qa["summary"] = "同一区域修订尚未验证：缺少定位对照、区域未改变、尺寸变化或原问题仍存在。"
        qa["issues"] = ##ERROR##[:11] + [{"code": "qa_incomplete", "severity": "HIGH", "region": "修订区域", "confidence": 1, "recommended_action": "请AI检查已保存的修改前后同一区域；不得仅因生成新图而通过。", "repair_type": "HUMAN_REVIEW"}]
    [i for i in qa.get("issues", [])]
    c = None; i = "VISUAL_CHECK_COMPLETE"

def preserve_outside_regions(before, after, targets):
    data = None; original, revised = [Image.open(io.BytesIO(data)).convert("RGBA") for data in (before, after)]
    if original.size != revised.size:
        if abs((original.width) / (original.height) - (revised.width) / (revised.height)) > 0.001:
            raise DomainError("REVISION_ALIGNMENT_REQUIRED", "修订图长宽比改变，无法可靠保留未修改区域；原稿已保留，请重新修订。")
        revised = revised.resize(original.size, Image.Resampling.LANCZOS)
    pixels = np.array(original).copy()
    
    updated = np.array(revised)
    for target in targets:
        x0, y0, x1, y1 = crop_box(target["bbox"], original.size)
        pixels[([y0:y1], [x0:x1])] = updated[([y0:y1], [x0:x1])]
    
    return png(Image.fromarray(pixels))
    
    data = None
