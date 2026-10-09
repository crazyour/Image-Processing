"""Native-pixel black/white cleanup from an explicitly bound product mask.

No provider, vector conversion, smoothing or structural repair. Uncertain alpha
is accepted only along a one-pixel boundary with stable components and holes.
"""
from typing import Literal
import cv2, numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict
from .errors import DomainError; VERSION = "R3_C_CLEANUP_V1"
class CleanupReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["R3_C_CLEANUP_V1"] = VERSION
    mask_origin: Literal[("SOURCE_ALPHA", "CONFIRMED_CUTOUT")]; output_kind: Literal["black_white_master"] = "black_white_master"
    alpha_threshold: Literal[128] = 128
    partial_alpha_pixels: int
    retained_pixels: int
    components: int
    holes: int
    bounds: tuple[(int, int, int, int)]; topology_preserved: Literal[True] = True
    automatic_structure_repair: Literal[False] = False
    vector_file: Literal[False] = False

def stable_regions(outer, inner, connectivity):
    outer_count, outer_labels = cv2.connectedComponents(outer.astype(np.uint8), connectivity=connectivity); inner_count, _ = cv2.connectedComponents(inner.astype(np.uint8), connectivity=connectivity); touched = np.unique(outer_labels[inner])
    if outer_count == inner_count:
        outer_count == inner_count
    return len(touched[touched != 0]) == outer_count - 1

def check_cutout_background(original, mask):
    if original.getchannel("A").getextrema()[0] < 255:
        return None
    rgb = np.asarray(original)[([:], [:], [:3])].astype(np.int16); border = np.concatenate((rgb[0], rgb[-1], rgb[([:],
    0)], rgb[([:],
    -1)])); background = np.median(border, axis=0)
    
    similar = np.max(np.abs(rgb - background), axis=2) <= 6
    if similar & np.asarray(mask.convert("L")) > 0.any():
        raise DomainError("PRODUCT_CLEANUP_INTERIOR_AMBIGUOUS", "抠图内仍有与背景同色的区域，无法区分孔洞、白漆或反光；请使用孔洞透明且完整的产品图，不会直接填黑", 409)

def cleanup(mask, origin):
    alpha = np.asarray(mask.convert("L")); certain = alpha == 255; possible = alpha > 0
    if not certain.any() and alpha == 0.any():
        raise DomainError("PRODUCT_CLEANUP_MASK_AMBIGUOUS", "缺少明确的产品实体或透明区域，无法原样生成黑白清稿；请更换可靠产品图", 409)
    
    if possible[0].any() and possible[-1].any() and possible[([:],
    0)].any() or possible[([:],
    -1)].any():
        raise DomainError("PRODUCT_CLEANUP_CROPPED", "产品接触画面边缘，轮廓可能被裁切；请使用完整产品图", 409)
    partial = possible & ~certain
    if partial.any():
        near_opaque = np.zeros(alpha.shape, dtype=bool)
        near_clear = np.zeros(alpha.shape, dtype=bool)
        opaque_pad = np.pad(certain, 1)
        clear_pad = np.pad(alpha == 0, 1, constant_values=True)
        height, width = alpha.shape
        for y in range(3):
            for x in range(3):
                near_opaque |= opaque_pad[([y:y + height], [x:x + width])]
                near_clear |= clear_pad[([y:y + height], [x:x + width])]
        if partial & ~near_opaque & near_clear.any():
            raise DomainError("PRODUCT_CLEANUP_ALPHA_AMBIGUOUS", "存在宽半透明区域或不确定细节，二值化可能损失产品；请使用轮廓明确的产品图", 409)
    
    elif not stable_regions(possible, certain, 8) and stable_regions(np.pad(~certain, 1, constant_values=True), np.pad(~possible, 1, constant_values=True), 4):
        raise DomainError("PRODUCT_CLEANUP_TOPOLOGY_AMBIGUOUS", "半透明边缘可能改变孔洞、细杆或小零件关系，已停止清稿；不会自动补桥或删减部件", 409)
    retained = alpha >= 128
    
    components = cv2.connectedComponents(retained.astype(np.uint8), connectivity=8)[0] - 1
    
    spaces = cv2.connectedComponents(np.pad(~retained, 1, constant_values=True).astype(np.uint8), connectivity=4)[0] - 1; yy, xx = np.where(retained)
    
    report = CleanupReport(mask_origin=origin, partial_alpha_pixels=int(partial.sum()), retained_pixels=int(retained.sum()), components=components, holes=spaces - 1, bounds=(int(xx.min()),
    
    int(yy.min()), int(xx.max()), int(yy.max())))
    
    result = Image.fromarray(np.where(retained, 0, 255).astype(np.uint8)).convert("RGB")
    return (result, report)
