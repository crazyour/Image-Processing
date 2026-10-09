"""Objective, read-only raster regression gate before any remote recheck."""
import hashlib, io, cv2, numpy as np
from PIL import Image
from .photo_product import structure_observation

def _labels(data):
    rgba = Image.open(io.BytesIO(data)).convert("RGBA"); canvas = Image.new("RGBA", rgba.size, "white"); canvas.alpha_composite(rgba); mask = np.asarray(canvas.convert("L")) < 128.astype(np.uint8)
    return cv2.connectedComponents(mask, connectivity=4)[1]

def structural_gate(before, after, targets, *, allow_detail_simplification, reject_no_change):
    regions
    
    c = r
    regions
    
    c = new["material_regions"] > 0
    same_size
    
    c = new.get("obvious_weak_neck")
    old.get("obvious_weak_neck")
    
    c = pixels_unchanged; d = None; r = None; r = None; c = r

def record_decision(db, job, asset, before, gate, accepted=False, error=None):
    info = dict(asset.info); info.update(revision_status="REVISION_REJECTED", revision_acceptance_gate=gate, previous_working_asset_id=before.id, current_working_asset_id=before.id, revision_rejection_reason=error); asset.info = info; job.snapshot = {"current_working_asset_id": before.id}
    if not accepted:
        asset.state = "NEEDS_HUMAN_DECISION"
        prior = dict(before.info)
        if prior.get("superseded_by_candidate") == asset.id:
            prior.pop("superseded_by_candidate", None)
        prior["current_working_asset_id"] = before.id
        before.info = prior
        if before.state in ("HISTORY", "AUTO_REPAIR"):
            before.state = "NEEDS_HUMAN_DECISION"
            return None
        return None
