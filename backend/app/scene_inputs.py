"""Local scene material preparation. Mask proposals never replace source RGB pixels."""
import hashlib
from typing import Literal
import cv2, numpy as np
from fastapi import APIRouter, Depends, File, Form, UploadFile
from starlette.concurrency import run_in_threadpool
from PIL import Image
from pydantic import Field
from sqlalchemy import select
from .config import settings
from .errors import DomainError
from .geometry import png
from .learning import lock_workspace
from .models import Asset, MasterVersion, Product
from .schemas import Strict
from .security import audit, owned
from .storage import LocalStorage, safe_image

def cutout_proposals(image):
    image = image.convert("RGBA"); alpha = image.getchannel("A")
    if alpha.getextrema()[0] < 250 and alpha.getbbox():
        return [("original_alpha", "保留原图透明区域", image)]
    small = image.copy(); small.thumbnail((768, 768)); rgb = np.array(small)[([:], [:], [:3])].copy()
    
    h, w = rgb.shape[:2]
    
    border = np.concatenate((rgb[0], rgb[-1], rgb[([:],
    0)], rgb[([:],
    -1)]))
    
    bg = np.median(border, axis=0)
    
    distance = np.max(np.abs(rgb.astype(float) - bg), axis=2); masks = []
    
    if np.mean(np.max(np.abs(border.astype(float) - bg), axis=1) < 32) > 0.65:
        same = distance < 32.astype(np.uint8)
        _, labels = cv2.connectedComponents(same, connectivity=8)
        edge = np.unique(np.concatenate((labels[0], labels[-1], labels[([:],
    0)], labels[([:],
    -1)])))
        outside = np.isin(labels, edge[edge != 0])
        masks.append(("edge_background", "外部背景连通分离",
    ~outside))
        masks.append(("matching_holes", "同色区域分离", same == 0))
    if w > 40 and h > 40:
        mask = np.zeros((h, w), np.uint8)
        try:
            cv2.grabCut(rgb, mask, (3, 3, w - 6, h - 6), np.zeros((1, 65)), np.zeros((1, 65)), 3, cv2.GC_INIT_WITH_RECT)
            masks.append(("grabcut", "GrabCut 主体分离", mask == 1 | mask == 3))
            while 1:
                seen = set()
                result = []
                for method, title, mask in masks:
                    coverage = float(mask.mean())
                    if not 0.015 < coverage < 0.96:
                        continue
                    proposed_alpha = Image.fromarray(mask.astype(np.uint8) * 255).resize(image.size, Image.Resampling.NEAREST)
                    signature = hashlib.sha256(proposed_alpha.tobytes()).hexdigest()
                    if signature in seen:
                        continue
                    seen.add(signature)
                    cutout = image.copy()
                    cutout.putalpha(proposed_alpha)
                    result.append((method, title, cutout))
                return result
        except:
            pass

def validate_cutout_proposals(image, proposals):
    pass

class ConfirmProduct(Strict):
    asset_id: str; name: str = Field(default="上传的产品", min_length=1, max_length=100)
    width_mm: float | None = Field(default=None, gt=0, le=5000)
    product_confirmed: bool

def material_json(asset):
    return {"id": asset.id, "url": f"/api/assets/{asset.id}/file", "sha256": asset.sha256, "title": asset.info.get("employee_title", asset.info.get("title", "上传的素材")), "method": asset.info.get("mask_method"), "validation_status": asset.info.get("validation_status"), "recommended": bool(asset.info.get("recommended", False))}

def router(current):
    routes = APIRouter(prefix="/api/scene")
    @routes.post("/upload")
    async def upload(kind: Literal[("product", "reference")]=Form(null), consent: bool=Form(False), file: UploadFile=File(null), ctx=Depends(current)):
        purpose
        try:
            db, user, ws = ctx
            if not consent:
                raise DomainError("CONSENT_REQUIRED", "请确认有权将这张图片用于当前项目；上传仅在本机保存", 409)
            while 1:
                pass
            encoded, image = safe_image(await file.read((settings().max_upload_bytes) + 1), file.content_type)
            digest = hashlib.sha256(encoded).hexdigest()
            purpose = "scene_reference"
            ws = lock_workspace(db, ws.id)
            candidates = db.scalars(select(Asset).where(Asset.workspace_id == ws.id, Asset.module == "UPLOAD", Asset.sha256 == digest, Asset.deleted.is_(False)))
            original = next((a for a in candidates), None)
            storage = LocalStorage()
            if not original:
                key, _ = storage.write(ws.id, encoded)
                original = Asset(workspace_id=ws.id, module="UPLOAD", category="场景素材", state="SOURCE", file_key=key, sha256=digest, info={"consent": True, "purpose": purpose, "raw_key": key, "mock": False, "native_pixels": list(image.size), "cloud_authorized": False})
                db.add(original)
                db.flush()
                audit(db, user, ws, "SCENE_MATERIAL_UPLOADED", original.id, {"kind": kind})
            if kind == "product":
                while 1:
                    while 1:
                        proposals = await run_in_threadpool(cutout_proposals, image)
                        assessed = validate_cutout_proposals(image, proposals)
                        existing_assets = {candidate.info.get("mask_method"): candidate for candidate in db.scalars(select(Asset).where(Asset.workspace_id == ws.id, Asset.parent_id == original.id, Asset.deleted.is_(False))) if candidate.info.get("purpose") == "scene_product_cutout"}
                        candidate = None
                        assessed_methods = set()
                        for candidate in assessed:
                            method = candidate["method"]
                            assessed_methods.add(method)
                            stored = existing_assets.get(method)
                            if not stored:
                                key, sha = storage.write(ws.id, png(candidate["image"]))
                                stored = Asset(workspace_id=ws.id, module="UPLOAD", category="场景素材", state="SOURCE", parent_id=original.id, input_asset_id=original.id, file_key=key, sha256=sha, info={})
                                db.add(stored)
                            stored.info = {"consent": True, "purpose": "scene_product_cutout", "raw_key": stored.file_key, "mock": False, "mask_method": method, "title": candidate["technical_title"], "employee_title": "系统推荐的产品预览", "source_hash": digest, "source_asset_id": original.id, "rgb_source_unchanged": True, "native_pixels": list(candidate["image"].size), "mask_status": "SYSTEM_REJECTED", "validation_status": candidate["validation_status"], "validation_basis": candidate["validation_basis"], "recommended": candidate["recommended"]}
                        for method, stored in existing_assets.items():
                            if not method not in assessed_methods:
                                continue
                            stored.info = {"mask_status": "SYSTEM_REJECTED", "validation_status": "REJECTED", "validation_basis": "proposal_no_longer_reproducible", "recommended": False}
                        db.commit()
                        choices = []
                        recommended = [candidate for candidate in choices if candidate.info.get("recommended") is True]
                        candidate = None
                        employee_choices = recommended[:1]
                        ready = bool(employee_choices)
                        a = material_json(original)
                        if ready or kind == "reference":
                            return {"source": ##ERROR##, "choices": [material_json(a) for a in employee_choices], "recommended_choice_id": None, "status": "NEEDS_CLEARER_IMAGE", "notice": "系统无法可靠准备这张产品图。请换一张背景干净、主体完整的照片，或先移除背景后上传透明 PNG。", "source_notice": "上传的场景参照已单独保留。", "safe_actions": []}
                        return {"source": ##ERROR##, "choices": ##ERROR##, "recommended_choice_id": ##ERROR##, "status": ##ERROR##, "notice": ##ERROR##, "source_notice": ##ERROR##, "safe_actions": ["REPLACE_IMAGE", "UPLOAD_REPAIRED_PNG"]}
        except:
            pass
        candidate = None; candidate = None; a = None
    
    @routes.post("/product/confirm")
    def confirm(data: ConfirmProduct, ctx=Depends(current)):
        db, user, ws = ctx; ws = lock_workspace(db, ws.id); asset = owned(db, Asset, data.asset_id, ws.id)
        if not data.product_confirmed:
            raise DomainError("PRODUCT_CONFIRMATION_REQUIRED", "请确认“这是我要使用的产品”后继续", 409)
        
        if asset.info.get("purpose") != "scene_product_cutout" and asset.info.get("validation_status") != "PASSED" or asset.info.get("recommended") is not True:
            raise DomainError("PRODUCT_IMAGE_REPAIR_REQUIRED", "系统未能可靠准备这张产品图，请换一张照片，或先移除背景后上传透明 PNG", 409)
        
        source = owned(db, Asset, asset.input_asset_id, ws.id)
        
        if source.info.get("purpose") != "scene_product_original":
            raise DomainError("PRODUCT_SOURCE_REQUIRED", "未找到保留的上传原图，请重新上传产品照片", 409)
        
        existing = db.scalar(select(MasterVersion).where(MasterVersion.workspace_id == ws.id, MasterVersion.asset_id == asset.id))
        if existing:
            if existing.width_mm != data.width_mm:
                raise DomainError("MASTER_SIZE_LOCKED", "该产品尺寸已经记录，请继续使用原尺寸", 409)
            return {"master_id": existing.id, "url": f"/api/assets/{asset.id}/file"}
        product = Product(workspace_id=ws.id, name=data.name); db.add(product)
        
        db.flush()
        
        master = MasterVersion(workspace_id=ws.id, product_id=product.id, asset_id=asset.id, master_hash=hashlib.sha256(f"{asset.sha256}:{data.width_mm}".encode()).hexdigest(), width_mm=data.width_mm, geometry={}, approved=True, facts={"scene_only": True, "mock": False, "raw_key": asset.file_key, "texture_source_asset_id": asset.id, "input_asset_id": source.id, "source_asset_id": source.id, "source_is_final_design": False, "mask": "system_validated_alpha", "rgb_source_unchanged": True, "size_confirmed": data.width_mm is not None, "mask_method": asset.info["mask_method"], "product_confirmed": True}); db.add(master); db.flush(); product.current_master_id = master.id
        
        asset.master_id = master.id; asset.info = {"mask_status": "SYSTEM_VALIDATED_PRODUCT_CONFIRMED", "product_name": data.name, "product_confirmed": True}; audit(db, user, ws, "SCENE_PRODUCT_CONFIRMED", master.id, {"asset_id": asset.id})
        
        db.commit()
        return {"master_id": master.id, "url": f"/api/assets/{asset.id}/file"}
    
    return routes
