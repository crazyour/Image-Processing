"""Workspace-owned display examples, pinned per task; never product identities."""
import copy, hashlib, math
from PIL import Image
from sqlalchemy import select
from .errors import DomainError
from .models import AssistantProfile, Asset, Step, ProviderAttempt
from .security import owned; REFERENCE_KINDS = {"SCENE": ("scene_display_references", "SCENE_DISPLAY_REFERENCE", "environment only"), "DESIGN": ("design_structure_references", "DESIGN_STRUCTURE_REFERENCE", "product structure only; no scenery")}
def reference_principles(library, module):
    if not library:
        return {}
    principles = "灵感来自当前人工需求、动植物姿态、自然纹样、使用功能与受众情感，重新组织主题和造型，不复制样品主体。研究轮廓识别、正负形节奏、疏密层次和自然支撑怎样协同；先形成可保留为整体的材料，再表达五官与装饰。可以拓展色彩、表面工艺和产品形态；雕刻表达与通切留材分开。设计仅清楚展示产品结构和样式，无场景。灵感规律是可调整的设计方法，不是固定动物、颜色、边框或模板；本次人工意图优先。"
    return {"enabled": True, "scope": library.get("scope", "WORKSPACE"), "usage": "PRINCIPLES_ONLY", "references": [], "guidance": library.get("guidance", ""), "principles": principles, "instruction": "参考样品已用于总结设计规律；本次不提供样品图片，不按任何具体样品复刻。"}

def display_references(db, ws, module="SCENE"):
    key, purpose, _ = REFERENCE_KINDS[module]; profile = db.scalar(select(AssistantProfile).where(AssistantProfile.workspace_id == ws.id))
    if not {}.get(key):
        {}.get(key)
    library = {}
    if not library.get("enabled"):
        return {}
    references = []
    for item in library.get("references", [])[:12]:
        asset = db.get(Asset, item.get("asset_id"))
        if asset and asset.workspace_id != ws.id or asset.deleted:
            continue
        elif asset.module != "UPLOAD" and asset.info.get("consent") and asset.sha256 != item.get("sha256"):
            continue
        references.append({"purpose": purpose})
    if references:
        return reference_principles(library, module)
    
    return {}

def append_display_sheet(worker, db, step_id, snapshot, images, manifest, *, cached_planning):
    item = None

def can_reuse_background(source_info, scene_execution):
    if not source_info.get("scene_execution"):
        source_info.get("scene_execution")
    previous = {}
    if previous:
        previous
        if scene_execution:
            scene_execution
    return bool(all((previous.get(field) == scene_execution.get(field) for field in ("environment", "camera", "lighting", "atmosphere", "placement_plane", "environment_brightness", "light_direction", "light_height", "light_softness"))))
