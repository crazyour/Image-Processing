"""Local connectivity checks and diagnostic rendering for black cutout images."""
from __future__ import annotations

import cv2
import numpy as np


_MAIN_COLOR = (235, 99, 37)  # RGB #2563eb in OpenCV BGR order.
_ISSUE_COLOR = (68, 68, 239)  # RGB #ef4444 in OpenCV BGR order.


class ConnectivityError(ValueError):
    """Raised when a generated image cannot be decoded for local inspection."""


def _black_mask(image_bytes: bytes) -> np.ndarray:
    encoded = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ConnectivityError("无法解码待检测的黑白图")

    if image.ndim == 2:
        gray = image
    elif image.shape[2] == 4:
        color = image[:, :, :3].astype(np.float32)
        alpha = image[:, :, 3:4].astype(np.float32) / 255.0
        flattened = color * alpha + 255.0 * (1.0 - alpha)
        gray = cv2.cvtColor(flattened.astype(np.uint8), cv2.COLOR_BGR2GRAY)
    else:
        gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)

    # The model is instructed to return hard black and white. A fixed midpoint
    # keeps the rule deterministic and treats antialiased light fringes as white.
    return np.where(gray < 128, 255, 0).astype(np.uint8)


def _components(mask: np.ndarray) -> tuple[int, np.ndarray, np.ndarray]:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8, ltype=cv2.CV_32S,
    )
    return count, labels, stats


def _issue_bbox(issue_mask: np.ndarray) -> list[int]:
    points = cv2.findNonZero(issue_mask)
    if points is None:
        return [0, 0, 0, 0]
    x, y, width, height = cv2.boundingRect(points)
    return [int(x), int(y), int(width), int(height)]


def _encode_png(image: np.ndarray, message: str) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ConnectivityError(message)
    return encoded.tobytes()


def _open_edit_corridor(editable: np.ndarray, issue_mask: np.ndarray,
                        target_mask: np.ndarray) -> None:
    """Open a compact masked corridor between an issue and the nearest target."""
    scale = min(editable.shape[:2])
    radius = max(10, round(scale * 0.018))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    editable[:] = cv2.bitwise_or(editable, cv2.dilate(issue_mask, kernel))

    issue_y, issue_x = np.where(issue_mask > 0)
    target_y, target_x = np.where(target_mask > 0)
    if not len(issue_x) or not len(target_x):
        return

    distance_input = np.where(target_mask > 0, 0, 255).astype(np.uint8)
    distances, nearest_labels = cv2.distanceTransformWithLabels(
        distance_input, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL,
    )
    source_index = int(np.argmin(distances[issue_y, issue_x]))
    source = (int(issue_x[source_index]), int(issue_y[source_index]))
    nearest_label = int(nearest_labels[source[1], source[0]])
    matching_targets = np.flatnonzero(nearest_labels[target_y, target_x] == nearest_label)
    if not len(matching_targets):
        return
    target_index = int(matching_targets[0])
    target = (int(target_x[target_index]), int(target_y[target_index]))
    thickness = max(14, round(scale * 0.028))
    cv2.line(editable, source, target, 255, thickness, cv2.LINE_8)
    cv2.circle(editable, source, thickness, 255, -1, cv2.LINE_8)
    cv2.circle(editable, target, thickness, 255, -1, cv2.LINE_8)


def create_repair_mask(image_bytes: bytes) -> bytes:
    """Create an RGBA mask whose transparent pixels are safe to regenerate."""
    mask = _black_mask(image_bytes)
    component_count, labels, stats = _components(mask)
    component_labels = list(range(1, component_count))
    component_labels.sort(key=lambda label: int(stats[label, cv2.CC_STAT_AREA]), reverse=True)
    editable = np.zeros_like(mask)
    if not component_labels:
        rgba = np.zeros((*mask.shape, 4), dtype=np.uint8)
        rgba[:, :, 3] = 255
        return _encode_png(rgba, "无法生成局部修复蒙版")

    main_label = component_labels[0]
    main_mask = np.where(labels == main_label, 255, 0).astype(np.uint8)
    for label in component_labels[1:]:
        issue_mask = np.where(labels == label, 255, 0).astype(np.uint8)
        _open_edit_corridor(editable, issue_mask, main_mask)

    eroded = cv2.erode(
        main_mask,
        cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)),
        iterations=1,
    )
    eroded_count, eroded_labels, eroded_stats = _components(eroded)
    robust_labels = list(range(1, eroded_count))
    robust_labels.sort(
        key=lambda label: int(eroded_stats[label, cv2.CC_STAT_AREA]), reverse=True,
    )
    if not robust_labels and int(stats[main_label, cv2.CC_STAT_AREA]) > 0:
        radius = max(10, round(min(mask.shape) * 0.018))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        editable = cv2.dilate(main_mask, kernel)
    elif len(robust_labels) > 1:
        robust_main = np.where(eroded_labels == robust_labels[0], 255, 0).astype(np.uint8)
        for label in robust_labels[1:]:
            issue_mask = np.where(eroded_labels == label, 255, 0).astype(np.uint8)
            _open_edit_corridor(editable, issue_mask, robust_main)

    rgba = np.zeros((*mask.shape, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255 - editable
    return _encode_png(rgba, "无法生成局部修复蒙版")


def remove_disconnected_islands(image_bytes: bytes) -> bytes:
    """Return a hard black/white PNG containing only the largest component."""
    mask = _black_mask(image_bytes)
    component_count, labels, stats = _components(mask)
    component_labels = list(range(1, component_count))
    component_labels.sort(key=lambda label: int(stats[label, cv2.CC_STAT_AREA]), reverse=True)
    cleaned = np.full(mask.shape, 255, dtype=np.uint8)
    if component_labels:
        cleaned[labels == component_labels[0]] = 0
    return _encode_png(cleaned, "无法执行本地孤岛清理")


def analyze_connectivity(image_bytes: bytes) -> tuple[dict, bytes]:
    """Return a connectivity report and color diagnostic.

    The largest 8-connected black component is the subject. Every other black
    component is an island. The subject is also eroded by one pixel to expose
    lobes joined only by a one-pixel neck; those lobes are reported as fragile
    connections and treated as repair targets.
    """
    mask = _black_mask(image_bytes)
    component_count, labels, stats = _components(mask)
    diagnostic = np.full((*mask.shape, 3), 255, dtype=np.uint8)
    issue_regions: list[dict] = []

    component_labels = list(range(1, component_count))
    component_labels.sort(key=lambda label: int(stats[label, cv2.CC_STAT_AREA]), reverse=True)
    if not component_labels:
        return {
            "status": "NO_BLACK_MATERIAL",
            "passed": False,
            "black_component_count": 0,
            "island_count": 0,
            "disconnected_island_count": 0,
            "fragile_connection_count": 0,
            "main_area": 0,
            "island_areas": [],
            "issue_regions": [],
        }, _encode_png(diagnostic, "无法生成连通性诊断图")

    main_label = component_labels[0]
    diagnostic[labels == main_label] = _MAIN_COLOR
    island_areas: list[int] = []
    for label in component_labels[1:]:
        issue_mask = np.where(labels == label, 255, 0).astype(np.uint8)
        diagnostic[labels == label] = _ISSUE_COLOR
        island_areas.append(int(stats[label, cv2.CC_STAT_AREA]))
        issue_regions.append({
            "type": "island",
            "bbox": _issue_bbox(issue_mask),
            "area": int(stats[label, cv2.CC_STAT_AREA]),
        })

    main_mask = np.where(labels == main_label, 255, 0).astype(np.uint8)
    eroded = cv2.erode(
        main_mask,
        cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)),
        iterations=1,
    )
    eroded_count, eroded_labels, eroded_stats = _components(eroded)
    robust_labels = list(range(1, eroded_count))
    robust_labels.sort(
        key=lambda label: int(eroded_stats[label, cv2.CC_STAT_AREA]), reverse=True,
    )

    fragile_count = max(0, len(robust_labels) - 1)
    if not robust_labels and int(stats[main_label, cv2.CC_STAT_AREA]) > 0:
        # A subject that disappears after one erosion consists entirely of
        # one-pixel material and is not safe as a cutout.
        fragile_count = 1
        diagnostic[main_mask > 0] = _ISSUE_COLOR
        issue_regions.append({
            "type": "fragile_connection",
            "bbox": _issue_bbox(main_mask),
            "area": int(stats[main_label, cv2.CC_STAT_AREA]),
        })
    for label in robust_labels[1:]:
        diagnostic[eroded_labels == label] = _ISSUE_COLOR
        issue_mask = np.where(eroded_labels == label, 255, 0).astype(np.uint8)
        issue_regions.append({
            "type": "fragile_connection",
            "bbox": _issue_bbox(issue_mask),
            "area": int(eroded_stats[label, cv2.CC_STAT_AREA]),
        })

    disconnected_count = len(component_labels) - 1
    issue_count = disconnected_count + fragile_count
    return {
        "status": "PASSED" if issue_count == 0 else "ISLANDS_FOUND",
        "passed": issue_count == 0,
        "black_component_count": len(component_labels),
        "island_count": issue_count,
        "disconnected_island_count": disconnected_count,
        "fragile_connection_count": fragile_count,
        "main_area": int(stats[main_label, cv2.CC_STAT_AREA]),
        "island_areas": island_areas,
        "issue_regions": issue_regions,
    }, _encode_png(diagnostic, "无法生成连通性诊断图")
