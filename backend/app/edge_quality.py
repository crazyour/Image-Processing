"""Bounded raster edge treatment of a frozen CLEANUP structure.

Only coverage changes inside eligible boundary pixels. The support and the
alpha-128 silhouette remain byte-for-byte identical to the CLEANUP mask.
"""
import hashlib
from typing import Literal
import cv2, numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict
from .errors import DomainError; VERSION = "R3_D_EDGE_QUALITY_V1"
class EdgeQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["R3_D_EDGE_QUALITY_V1"] = VERSION
    output_kind: Literal["edge_clean"] = "edge_clean"
    output_format: Literal["BLACK_RGBA_PNG"] = "BLACK_RGBA_PNG"
    algorithm: Literal["PROTECTED_INNER_COVERAGE_V1"] = "PROTECTED_INNER_COVERAGE_V1"
    alpha_threshold: Literal[128] = 128
    filter_radius_pixels: Literal[1] = 1
    small_feature_area_limit_pixels: Literal[16] = 16
    corner_protection_radius_pixels: Literal[2] = 2
    corner_angle_limit_degrees: Literal[100] = 100
    native_pixels: tuple[(int, int)]
    edge_pixels: int
    white_matte_edge_pixels_cleared: int; color_fringe_pixels: Literal[0] = 0
    color_fringe_status: Literal["NOT_PRESENT_IN_BINARY_CLEANUP"] = "NOT_PRESENT_IN_BINARY_CLEANUP"
    antialias_pixels: int
    antialias_status: Literal[("APPLIED", "NO_ELIGIBLE_EDGE")]
    small_holes_protected: int
    small_parts_protected: int
    thin_feature_pixels_protected: int
    sharp_corner_pixels_protected: int
    protected_pixels: int
    components: int
    holes: int
    source_binary_sha256: str
    result_binary_sha256: str
    source_support_sha256: str
    result_support_sha256: str
    transparent_pixels: int; transparent_rgb_zero: Literal[True] = True
    protected_pixels_unchanged: Literal[True] = True
    structure_changed_pixels: Literal[0] = 0
    source_overwritten: Literal[False] = False
    automatic_structure_repair: Literal[False] = False
    manufacturing_verified: Literal[False] = False

def mask_hash(mask):
    return hashlib.sha256(mask.astype(np.uint8).tobytes()).hexdigest()

def neighbors(mask):
    padded = np.pad(mask, 1); h, w = mask.shape
    for y in range(3):
        for x in range(3):
            if not (x, y) != (1, 1):
                continue
    x = None; y = None
    return ##ERROR##[padded[([y:y + h], [x:x + w])]]
    
    y; x = None; y = x

def near(mask):
    return np.logical_or.reduce([mask])

def edge_quality(source, frozen_mask):
    if source.size != frozen_mask.size:
        raise DomainError("EDGE_SOURCE_MISMATCH", "清稿与绑定蒙版尺寸不一致，请恢复对应版本", 409)
    rgba = np.asarray(source.convert("RGBA")); retained = np.asarray(frozen_mask.convert("L")) >= 128; rgb = rgba[([:], [:], [:3])]
    
    if not np.all(rgba[([:], [:],
    3)] == 255) and np.all(rgb == 0 | rgb == 255) and np.array_equal(rgb[([:], [:],
    0)], rgb[([:], [:],
    1)]) and np.array_equal(rgb[([:], [:],
    0)], rgb[([:], [:],
    2)]) and np.array_equal(rgb[([:], [:],
    0)] == 0, retained):
        raise DomainError("EDGE_SOURCE_MISMATCH", "当前文件不是与蒙版一致的黑白清稿，无法安全处理色边；请恢复有效 CLEANUP 版本", 409)
    
    if retained.any() and retained[0].any() and retained[-1].any() and retained[([:],
    0)].any() or retained[([:],
    -1)].any():
        raise DomainError("EDGE_SOURCE_CROPPED", "清稿为空或接触画面边缘，请使用轮廓完整的清稿", 409)
    adjacent = neighbors(retained)
    
    edge = retained & ~np.logical_and.reduce(adjacent)
    
    outer_edge = ~retained & np.logical_or.reduce(adjacent); protected = np.zeros(retained.shape, bool)
    
    count, labels, stats, _ = cv2.connectedComponentsWithStats(retained.astype(np.uint8), connectivity=8)
    
    small_part_ids = np.flatnonzero(stats[([1:],
    cv2.CC_STAT_AREA)] <= 16) + 1; protected |= np.isin(labels, small_part_ids)
    
    spaces, space_labels, space_stats, _ = cv2.connectedComponentsWithStats(~retained.astype(np.uint8), connectivity=4); exterior = int(space_labels[(0, 0)]); hole_ids = [i for i in range(1, spaces) if not i != exterior]; i = None
    
    small_hole_ids = [i for i in hole_ids if not space_stats[(i, cv2.CC_STAT_AREA)] <= 16]; i = None
    
    protected |= near(np.isin(space_labels, small_hole_ids)) & retained; p = np.pad(retained, 1)
    
    thin = retained & ~p[([:-2], [1:-1])] & ~p[([2:],
    [1:-1])] | ~p[([1:-1], [:-2])] & ~p[([1:-1], [2:])] | ~p[([:-2], [:-2])] & ~p[([2:],
    [2:])] | ~p[([:-2], [2:])] & ~p[([2:], [:-2])]; protected |= near(thin) & retained
    
    contours, _ = cv2.findContours(retained.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    
    corner_centers = np.zeros(retained.shape, np.uint8)
    for contour in contours:
        points = contour[([:],
    0,
    [:])].astype(np.float32)
        if len(points) < 9:
            for x, y in points.astype(int):
                corner_centers[(y, x)] = 1
            continue
        b = np.roll(points, -4, axis=0) - points
        a = np.roll(points, 4, axis=0) - points
        norms = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
        cosine = np.divide(a * b.sum(axis=1), norms, out=np.ones_like(norms), where=norms > 0)
        for x, y in points[cosine >= np.cos(np.deg2rad(100))].astype(int):
            corner_centers[(y, x)] = 1
    protected |= near(near(corner_centers.astype(bool))) & retained
    
    binary = retained.astype(np.float32); gx = cv2.Sobel(binary, cv2.CV_32F, 1, 0, ksize=3)
    
    gy = cv2.Sobel(binary, cv2.CV_32F, 0, 1, ksize=3)
    
    eligible = edge & ~protected & np.abs(gx) > 0 & np.abs(gy) > 0
    
    coverage = cv2.sepFilter2D(binary, cv2.CV_32F, np.array([0.25, 0.5, 0.25], np.float32), np.array([0.25, 0.5, 0.25], np.float32)); alpha = retained.astype(np.uint8) * 255
    alpha[eligible] = np.maximum(128, np.rint(coverage[eligible] * 255)).astype(np.uint8)
    
    changed = alpha != retained.astype(np.uint8) * 255
    
    if np.array_equal(alpha > 0, retained) and np.array_equal(alpha >= 128, retained) and np.any(alpha[protected] != 255) or np.any(changed & ~edge):
        raise DomainError("EDGE_STRUCTURE_CHANGED", "候选结果未通过原结构核对，未保存；不会自动修改设计", 409)
    
    result = np.zeros([4], np.uint8)
    result[([:], [:],
        3)] = alpha
    
    report = EdgeQualityReport(native_pixels=source.size, edge_pixels=int(edge.sum()), white_matte_edge_pixels_cleared=int(outer_edge.sum()), antialias_pixels=int(changed.sum()), antialias_status="NO_ELIGIBLE_EDGE", small_holes_protected=len(small_hole_ids), small_parts_protected=len(small_part_ids), thin_feature_pixels_protected=int(thin.sum()), sharp_corner_pixels_protected=int(corner_centers.sum()), protected_pixels=int(protected.sum()), components=count - 1, holes=len(hole_ids), source_binary_sha256=mask_hash(retained), result_binary_sha256=mask_hash(alpha >= 128), source_support_sha256=mask_hash(retained), result_support_sha256=mask_hash(alpha > 0), transparent_pixels=int(alpha == 0.sum()))
    return (Image.fromarray(result), report)
    
    i = None; i = None
