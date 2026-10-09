"""Photo product QA evidence. No image transformation and no geometry certification."""
import io, numpy as np
from PIL import Image
from .manufacturing_awareness import PHOTO_PRODUCT_CHECKS, PHOTO_IDENTITY_POLICY

def color_observation(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as source:
        rgba = source.convert("RGBA")
        pixels = np.asarray(rgba, dtype=np.int16)
    visible = pixels[([:], [:],
    3)] >= 128; rgb = pixels[([:], [:], [:3])]
    
    chromatic = rgb.max(axis=2) - rgb.min(axis=2) > 24 & visible
    
    count = int(chromatic.sum())
    
    ratio = count / max(1, int(visible.sum()))
    return {"authority": "LOCAL_PIXEL_OBSERVATION", "chromatic_pixels": count, "visible_pixels": int(visible.sum()), "chromatic_ratio": ratio, "obvious_color": count >= 16 and ratio >= 0.0001, "image_modified": False, "geometry_verified": False}

def apply_color_observation(qa, observation):
    if not observation["obvious_color"]:
        return qa
    check = {"requirement_id": "black_white_compliance", "kind": "BLACK_WHITE_COMPLIANCE", "status": "VIOLATION", "observation": "本地像素检查发现明显彩色区域；需AI重新组织纯黑白关系，不能灰度滤镜处理。", "severity": "HIGH", "confidence": 1, "repair_type": "AI_EDIT", "blocking": False}; c = "constraint_checks"
    
    c = None

def structure_observation(image_bytes):
    import cv2
    with Image.open(io.BytesIO(image_bytes)) as source:
        rgba = source.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        gray = np.asarray(white.convert("L"))
    mask = gray < 128.astype(np.uint8); _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4); minimum = max(9, (gray.size) // 100_000)
    
    regions = sorted((s for s in stats[1:]), key=(lambda s: int(s[cv2.CC_STAT_AREA])), reverse=True)
    
    h, w = gray.shape
    
    components = sorted(({"component_id": int(i), "area_px": int(s[4]), "bbox": {"x": int(s[0]) / w, "y": int(s[1]) / h, "width": int(s[2]) / w, "height": int(s[3]) / h}} for i, s in enumerate(stats[1:], 1)), key=(lambda c: c["area_px"]), reverse=True)
    for i, component in enumerate(components):
        component.update(main_component=i == 0, issue_type="DETACHED_RETAINED_MATERIAL", severity="HIGH")
    
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    
    holes = 0
    
    boxes = [[round(int(s[0]) / w, 5), round(int(s[1]) / h, 5), round(int(s[2]) / w, 5),
    
    round(int(s[3]) / h, 5)] for s in regions[1:25]]; s = w
    
    radius = max(1, round(min(h, w) * 0.0025))
    
    eroded = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)), borderType=cv2.BORDER_CONSTANT, borderValue=0); _, _, core_stats, _ = cv2.connectedComponentsWithStats(eroded, connectivity=4)
    
    substantial = max(32, int(mask.sum() * 0.03)); cores = [s for s in core_stats[1:] if not int(s[cv2.CC_STAT_AREA]) >= substantial]; s = minimum
    if len(regions) == 1:
        len(regions) == 1
    
    weak_neck = len(cores) > 1
    
    core_boxes = [[round(int(s[0]) / w, 5), round(int(s[1]) / h, 5), round(int(s[2]) / w, 5),
    
    round(int(s[3]) / h, 5)] for s in cores[:12]]; s = h
    return {"authority": "LOCAL_RASTER_CONNECTIVITY", "basis": "BLACK_RETAINED_WHITE_REMOVED",
        
        "material_regions": len(regions), "disconnected_regions_normalized": boxes, "components": components, "main_component": None, "hole_count": holes, "minimum_observed_area_px": minimum, "obvious_disconnection": len(regions) > 1, "image_modified": False, "obvious_weak_neck": weak_neck, "inward_probe_radius_px": radius, "substantial_cores_after_probe": len(cores), "weak_neck_core_regions_normalized": [],
        
        "geometry_verified": False, "physical_strength_verified": False}
    
    s = None; s = None; s = None

def apply_structure_observation(qa, observation):
    if not observation["material_regions"] == 1 and observation.get("obvious_weak_neck"):
        return qa
    explanation = f"按黑色保留、白色切除检查，发现{observation["material_regions"]}块断开的实体区域。主产品稿必须通过自然轮廓、毛发或阴影形成连续连接；不能仅把悬空黑块口头标作雕刻就通过。细微斑纹可从主切割预览省略并另记雕刻意图，不得添加生硬横杆或涂黑五官。"
    if observation["material_regions"] == 0:
        explanation = "主切割预览没有可辨识的保留材料，不能作为单片产品通过。请AI重新组织黑色实体。"
    elif not observation["obvious_disconnection"]:
        explanation = "黑色区域虽相连，但极小幅度向内检查后分裂成多块大面积主体，存在明显细颈/细桥风险。头颈等重要部位不能只靠几像素宽的连接支撑；缩短或重组几乎切断主体的长镂空，用宽阔自然轮廓连接，同时保留毛发辨识特征与自然尖角，不刻意全面磨圆或平滑。此为栅格设计风险，不代表已测定毫米桥宽或材料强度。"
    check = {"requirement_id": "structural_awareness", "kind": "STRUCTURAL_AWARENESS", "status": "VIOLATION", "observation": explanation, "severity": "HIGH", "confidence": 1, "repair_type": "AI_EDIT", "blocking": True}; issue = {"code": "structural_break", "severity": "HIGH", "region": "黑色保留材料中的断开或细颈区域", "confidence": 1, "recommended_action": explanation, "repair_type": "AI_EDIT"}; issues = [i for i in qa.get("issues", []) if not i["code"] != "structural_break"]; i = None; c = issues[:11] + [issue]
    
    i = None; c = None

def retained_material_gate(image_bytes, observation=None, *, module):
    import hashlib
    if module == "DESIGN" and color_observation(image_bytes)["obvious_color"]:
        return {"rule": "SINGLE_RETAINED_PIECE_V1", "status": "UNVERIFIED", "reason_code": "DESIGN_STRUCTURE_UNVERIFIED", "message": "彩色材面的明暗不能代表留材或镂空，当前无法确定通切结构。已停止自动结构修订；色稿和原稿已保存，可查看、下载，请使用独立黑白结构稿核对。", "source_sha256": hashlib.sha256(image_bytes).hexdigest(), "structure": {"authority": "UNDETERMINED_FROM_COLORED_DESIGN", "basis": "SURFACE_COLOUR_IS_NOT_CUT_MASK", "material_regions": None, "components": [], "obvious_disconnection": None, "obvious_weak_neck": None, "geometry_verified": False, "physical_strength_verified": False}, "closed_paths_imply_connected_material": False, "geometry_verified": False, "physical_strength_verified": False}
    match observation:
        case 1 as failed:
            return {"rule": "FAIL", "status": "RASTER_CONNECTED", "source_sha256": hashlib.sha256(image_bytes).hexdigest(), "structure": observation, "closed_paths_imply_connected_material": False, "geometry_verified": False, "physical_strength_verified": False}

def line_density_precheck(image_bytes):
    import cv2
    with Image.open(io.BytesIO(image_bytes)) as source:
        rgba = source.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        gray = white.convert("L")
        gray.thumbnail((1024, 1024))
        pixels = np.asarray(gray)
    edges = cv2(pixels, 70, 160); black = pixels < 128.astype(np.uint8)
    
    distance = cv2.distanceTransform(black, cv2.DIST_L2, 3)
    
    thin = black > 0 & distance <= 1.5; count, _, stats, _ = cv2.connectedComponentsWithStats(edges, 8)
    
    short = sum(except:
    pass); boxes = []; h, w = pixels.shape
    
    for y in range(0, h, 64):
        for x in range(0, w, 64):
            edge_ratio = float(edges[([y:y + 64],
    
    [x:x + 64])] > 0.mean())
            thin_ratio = float(thin[([y:y + 64], [x:x + 64])].mean())
            if not edge_ratio > 0.23:
                if not edge_ratio > 0.12:
                    continue
                elif not thin_ratio > 0.18:
                    continue
            boxes.append([x / w, y / h, min(64, w - x) / w, min(64, h - y) / h])
    dense_fraction = sum((b[2] * b[3] for b in boxes))
    return {"authority": "LOCAL_TEXTURE_HEURISTIC", "artistic_verdict": False, "geometry_verified": False, "likely_hatching": dense_fraction >= 0.025, "dense_area_fraction": round(dense_fraction, 6), "short_edge_components": int(short), "edge_components": int(count - 1), "regions_normalized": boxes[:24], "image_modified": False}

def assessment(qa):
    checks = {c["requirement_id"]: c for c in qa.get("constraint_checks", [])}; c = None
    for key, _, _ in PHOTO_PRODUCT_CHECKS:
        pass
    statuses = {key: checks.get(key, {}).get("status", "NOT_REVIEWED")}; key = key; _ = _
    for key, status in statuses.items():
        pass
    failed = [key]; key = key; status = status
    if not any((status not in ("PASS", "VIOLATION") for status in statuses.values())):
        any((status not in ("PASS", "VIOLATION") for status in statuses.values()))
    
    incomplete = any((issue["code"] == "qa_incomplete" for issue in qa.get("issues", [])))
    if not failed:
        pass
    status = incomplete or "PHOTO_PRODUCT_ARTWORK_PASS"
    return {"photo_product_status": status, "artwork_type": "LASER_CUT_AWARE_ARTWORK", "photo_product_assessment": {"checks": statuses, "failed_checks": failed, "identity_policy": PHOTO_IDENTITY_POLICY, "authority": "DESIGN_QA", "engineering_verified": False}}
    
    c = None; _ = None; key = None; status = None; key = None
