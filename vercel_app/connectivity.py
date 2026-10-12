"""Local connectivity checks and diagnostic rendering for black cutout images."""
from __future__ import annotations

import cv2
import numpy as np


_MAIN_COLOR = (235, 99, 37)  # RGB #2563eb in OpenCV BGR order.
_ISLAND_COLORS = (
    (68, 68, 239),    # red
    (94, 163, 34),    # green
    (168, 85, 247),   # magenta
    (11, 158, 245),   # orange
    (211, 191, 6),    # cyan
    (246, 130, 147),  # purple
)


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


def analyze_connectivity(image_bytes: bytes) -> tuple[dict, bytes]:
    """Return a JSON-ready report and a PNG diagnostic image.

    The largest 8-connected black component is the subject. Every other black
    component is an island. The subject is also eroded by one pixel to expose
    lobes joined only by a one-pixel neck; those lobes are reported as fragile
    connections and treated as repair targets.
    """
    mask = _black_mask(image_bytes)
    component_count, labels, stats = _components(mask)
    diagnostic = np.full((*mask.shape, 3), 255, dtype=np.uint8)

    component_labels = list(range(1, component_count))
    component_labels.sort(key=lambda label: int(stats[label, cv2.CC_STAT_AREA]), reverse=True)
    if not component_labels:
        ok, encoded = cv2.imencode(".png", diagnostic)
        if not ok:
            raise ConnectivityError("无法生成连通性诊断图")
        return {
            "status": "NO_BLACK_MATERIAL",
            "passed": False,
            "black_component_count": 0,
            "island_count": 0,
            "disconnected_island_count": 0,
            "fragile_connection_count": 0,
            "main_area": 0,
            "island_areas": [],
        }, encoded.tobytes()

    main_label = component_labels[0]
    diagnostic[labels == main_label] = _MAIN_COLOR
    island_areas: list[int] = []
    for index, label in enumerate(component_labels[1:]):
        diagnostic[labels == label] = _ISLAND_COLORS[index % len(_ISLAND_COLORS)]
        island_areas.append(int(stats[label, cv2.CC_STAT_AREA]))

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
    for index, label in enumerate(robust_labels[1:]):
        color_index = len(island_areas) + index
        diagnostic[eroded_labels == label] = _ISLAND_COLORS[color_index % len(_ISLAND_COLORS)]

    disconnected_count = len(component_labels) - 1
    issue_count = disconnected_count + fragile_count
    ok, encoded = cv2.imencode(".png", diagnostic)
    if not ok:
        raise ConnectivityError("无法生成连通性诊断图")

    return {
        "status": "PASSED" if issue_count == 0 else "ISLANDS_FOUND",
        "passed": issue_count == 0,
        "black_component_count": len(component_labels),
        "island_count": issue_count,
        "disconnected_island_count": disconnected_count,
        "fragile_connection_count": fragile_count,
        "main_area": int(stats[main_label, cv2.CC_STAT_AREA]),
        "island_areas": island_areas,
    }, encoded.tobytes()
