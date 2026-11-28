"""Conservative STL geometry smoothing with a separately verified output.

Uniform Taubin lambda/mu steps move actual vertices, not shading normals. Sharp
edges and open boundaries are frozen; no semantic (hair/skin) selection is made.
The original byte stream is never modified. All limits apply to the exported
float32 geometry, including optional one-level midpoint subdivision.
"""
from __future__ import annotations

import hashlib
import struct
import time

import numpy as np

from . import mesh_quality as mesh

SMOOTHER_VERSION = "stl-taubin-v1"
MAX_SMOOTH_BYTES = 64 * 1024 * 1024
MAX_SMOOTH_FACES = 1_000_000
SMOOTH_SECONDS = 90
_SETTINGS = {
    "gentle": {"iterations": 6, "global_cap": .0025, "local_cap": .18,
               "dimension_limit": .01, "volume_limit": .025},
    "standard": {"iterations": 12, "global_cap": .005, "local_cap": .25,
                 "dimension_limit": .02, "volume_limit": .05},
}
LIMITATIONS = [
    "仅按网格几何保护开放边界和明显尖锐边，不能自动识别皮肤、头发、五官或设计细节；请对比原版。",
    "轻度平滑不能恢复已丢失的细节，仍可能改变细小沟槽；原始 STL 保留，平滑结果另存副本。",
    "未检查自相交、壁厚、支撑或与参考图的一致性；复检通过不等于可打印认证。STL 不包含单位。",
    "可选细分仅做一级三角中点细分，再移动顶点平滑；单独增加面数不会使曲面变圆。",
]


class _Rejected(Exception):
    pass


def _labels(count: int, pairs: np.ndarray, deadline: float) -> np.ndarray:
    """Connected components with vectorized root hooking and path compression."""
    parent = np.arange(count, dtype=np.int32)
    if not len(pairs):
        return parent
    while True:
        mesh._tick(deadline)
        a, b = parent[pairs[:, 0]], parent[pairs[:, 1]]
        active = a != b
        if not np.any(active):
            return parent
        np.minimum.at(parent, np.maximum(a[active], b[active]), np.minimum(a[active], b[active]))
        while True:
            compressed = parent[parent]
            if np.array_equal(parent, compressed):
                break
            parent = compressed
            mesh._tick(deadline)


def _edges(faces: np.ndarray, deadline: float) -> dict:
    directed = faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    forward = directed[:, 0] < directed[:, 1]
    directed.sort(axis=1)
    keys = (directed[:, 0].astype(np.uint64) << np.uint64(32)) | directed[:, 1].astype(np.uint64)
    _, first, inverse, counts = np.unique(keys, return_index=True, return_inverse=True, return_counts=True)
    order = np.argsort(inverse, kind="stable")
    starts = np.r_[0, np.cumsum(counts[:-1])]
    shared = starts[counts == 2]
    occurrences = np.column_stack((order[shared], order[shared + 1])).astype(np.int32)
    mesh._tick(deadline)
    return {"vertices": directed[first], "inverse": inverse.reshape(-1, 3).astype(np.int32),
            "counts": counts.astype(np.int32), "occurrences": occurrences, "forward": forward,
            "shared_ids": np.flatnonzero(counts == 2).astype(np.int32)}


def _validate_vertex_fans(faces: np.ndarray, data: dict, deadline: float) -> None:
    """Reject bow-tie/pinched vertices, including closed shells sharing a point."""
    occurrences = data["occurrences"]
    low = np.where(data["forward"][occurrences], occurrences,
                   occurrences // 3 * 3 + (occurrences + 1) % 3)
    high = np.where(data["forward"][occurrences],
                    occurrences // 3 * 3 + (occurrences + 1) % 3, occurrences)
    labels = _labels(faces.size, np.concatenate((low, high)), deadline)
    # Every corner at a vertex must belong to the same edge-connected fan.
    vertex_count = int(faces.max()) + 1
    smallest = np.full(vertex_count, faces.size, dtype=np.int32)
    largest = np.zeros(vertex_count, dtype=np.int32)
    np.minimum.at(smallest, faces.ravel(), labels)
    np.maximum.at(largest, faces.ravel(), labels)
    if np.any(smallest != largest):
        raise _Rejected("检测到非流形顶点（同一点连接多个不连续面扇），请先修复网格。")


def _protection(vertices: np.ndarray, faces: np.ndarray, data: dict) -> tuple[np.ndarray, np.ndarray]:
    normals = np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                       vertices[faces[:, 2]] - vertices[faces[:, 0]])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    pair_faces = data["occurrences"] // 3
    dot = np.einsum("ij,ij->i", normals[pair_faces[:, 0]], normals[pair_faces[:, 1]])
    protected_edges = data["counts"] != 2
    # 50-degree dihedral threshold intentionally preserves coarse corners.
    protected_edges[data["shared_ids"][dot < np.cos(np.deg2rad(50.0))]] = True
    protected = np.zeros(len(vertices), dtype=bool)
    protected[data["vertices"][protected_edges].ravel()] = True
    return protected, protected_edges


def _subdivide(vertices: np.ndarray, faces: np.ndarray, data: dict,
               protected: np.ndarray, protected_edges: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    midpoints = (vertices[data["vertices"][:, 0]] + vertices[data["vertices"][:, 1]]) / 2
    a, b, c = faces.T
    ab, bc, ca = (data["inverse"] + len(vertices)).T
    # Children retain their original face's winding and occur consecutively.
    children = np.stack((np.column_stack((a, ab, ca)), np.column_stack((ab, b, bc)),
                         np.column_stack((ca, bc, c)), np.column_stack((ab, bc, ca))), axis=1)
    return (np.concatenate((vertices, midpoints)), children.reshape(-1, 3).astype(np.int32),
            np.concatenate((protected, protected_edges)))


def _taubin(vertices: np.ndarray, edges: np.ndarray, protected: np.ndarray,
            caps: np.ndarray, iterations: int, deadline: float) -> np.ndarray:
    original = vertices.copy()
    current = vertices.copy()
    degree = np.bincount(edges.ravel(), minlength=len(vertices)).astype(np.float64)
    for _ in range(iterations):
        for coefficient in (.5, -.53):
            mesh._tick(deadline)
            total = np.empty_like(current)
            for axis in range(3):
                total[:, axis] = (
                    np.bincount(edges[:, 0], weights=current[edges[:, 1], axis], minlength=len(vertices))
                    + np.bincount(edges[:, 1], weights=current[edges[:, 0], axis], minlength=len(vertices))
                )
            candidate = current + coefficient * (total / degree[:, None] - current)
            if coefficient > 0:
                # Project only after the complete lambda/mu pair. Clipping the
                # contraction alone defeats Taubin's shrinkage compensation.
                current = candidate
                current[protected] = original[protected]
                continue
            displacement = candidate - original
            distance = np.linalg.norm(displacement, axis=1)
            factor = np.minimum(1., np.divide(caps, distance, out=np.ones_like(caps), where=distance > 0))
            current = original + displacement * factor[:, None]
            current[protected] = original[protected]
    return current


def _volume_ratios(before: np.ndarray, after: np.ndarray, faces: np.ndarray,
                   face_labels: np.ndarray) -> float | None:
    """Per-component signed volumes; decline to call cancellation reliable."""
    _, labels = np.unique(face_labels, return_inverse=True)
    triangles = before[faces]
    terms = np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])) / 6
    volumes = np.bincount(labels, weights=terms)
    absolute = np.bincount(labels, weights=np.abs(terms))
    if np.any(np.abs(volumes) <= np.maximum(absolute * 1e-10, np.finfo(float).tiny)):
        return None
    triangles = after[faces]
    new_terms = np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])) / 6
    new_volumes = np.bincount(labels, weights=new_terms)
    if np.any(new_volumes * volumes <= 0):
        raise _Rejected("平滑副本的闭合部件体积方向发生变化，已取消输出。")
    return float(np.max(np.abs(new_volumes - volumes) / np.abs(volumes)))


def process_smoothing(raw: bytes, *, strength: str = "gentle", subdivide: bool = False) -> tuple[dict, bytes | None]:
    """Return a JSON report and only a geometrically verified separate STL copy."""
    start = time.monotonic()
    report = {"version": SMOOTHER_VERSION, "source_sha256": hashlib.sha256(raw).hexdigest(),
              "strength": strength, "subdivide": subdivide, "status": "incomplete", "message": "",
              "stats": {}, "before": None, "after": None, "limitations": list(LIMITATIONS), "elapsed_ms": 0}

    def finish(status, message, output=None):
        report.update(status=status, message=message, elapsed_ms=round((time.monotonic() - start) * 1000))
        return report, output

    if not isinstance(strength, str) or strength not in _SETTINGS or not isinstance(subdivide, bool):
        return finish("invalid", "平滑参数无效；强度仅支持 gentle / standard，细分必须为布尔值。")
    if len(raw) > MAX_SMOOTH_BYTES:
        return finish("incomplete", "文件超过 64 MiB 平滑上限，请使用桌面网格工具；原文件已保留。")
    if not mesh._SLOT.acquire(blocking=False):
        return finish("incomplete", "当前实例正在处理其他模型，请稍后重试；原文件已保留。")
    deadline = start + SMOOTH_SECONDS
    try:
        mesh._tick(deadline)
        if len(raw) >= 84 and 84 + struct.unpack_from("<I", raw, 80)[0] * 50 == len(raw):
            count = struct.unpack_from("<I", raw, 80)[0]
            if count * (4 if subdivide else 1) > MAX_SMOOTH_FACES:
                raise mesh.CheckLimit("输入或细分后将超过 100 万面上限；未减面、未生成副本。")
        before, triangles, _ = mesh._inspect(raw, deadline)
        before["sha256"] = report["source_sha256"]
        report["before"] = before
        stats = report["stats"]
        stats["faces_before"] = before["stats"]["faces"]
        count = len(triangles) * (4 if subdivide else 1)
        if count > MAX_SMOOTH_FACES or 84 + count * 50 > MAX_SMOOTH_BYTES:
            raise mesh.CheckLimit("输入或细分后将超过平滑文件或 100 万面上限；未减面、未生成副本。")
        if any(before["stats"][key] for key in ("degenerate_faces", "duplicate_faces", "nonmanifold_edges", "inconsistent_edges")):
            raise _Rejected("网格含零面积面、重复面、非流形边或朝向不一致，请先使用修复或桌面网格工具处理。")
        vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
        faces = inverse.reshape(-1, 3).astype(np.int32)
        stats["vertices_before"] = len(vertices)
        del triangles, inverse
        dimensions = np.ptp(vertices, axis=0)
        scale = float(np.linalg.norm(dimensions))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("模型尺寸无效，无法平滑。")
        origin = vertices.min(axis=0) + dimensions / 2
        data = _edges(faces, deadline)
        _validate_vertex_fans(faces, data, deadline)
        face_labels = _labels(len(faces), data["occurrences"] // 3, deadline)
        # A separate open sheet must not exempt closed components from volume
        # checks. Identify closure per connected component before subdivision.
        boundary_faces = np.any(data["counts"][data["inverse"]] == 1, axis=1)
        open_labels = np.unique(face_labels[boundary_faces])
        closed_faces = ~np.isin(face_labels, open_labels)
        stats["volume_checked_components"] = len(np.unique(face_labels[closed_faces]))
        protected, protected_edges = _protection((vertices - origin) / scale, faces, data)
        if subdivide:
            vertices, faces, protected = _subdivide(vertices, faces, data, protected, protected_edges)
            face_labels = np.repeat(face_labels, 4)
            closed_faces = np.repeat(closed_faces, 4)
            data = _edges(faces, deadline)
        stats.update(faces_after=len(faces), vertices_after=len(vertices), protected_vertices=int(protected.sum()))
        normalized = (vertices - origin) / scale
        edges = data["vertices"]
        del data, protected_edges
        lengths = np.linalg.norm(normalized[edges[:, 0]] - normalized[edges[:, 1]], axis=1)
        local = np.full(len(vertices), np.inf)
        np.minimum.at(local, edges[:, 0], lengths)
        np.minimum.at(local, edges[:, 1], lengths)
        settings = _SETTINGS[strength]
        caps = np.minimum(settings["global_cap"], local * settings["local_cap"])
        caps[protected] = 0
        stats.update(max_allowed_displacement=float(caps.max() * scale),
                     dimension_change_limit=settings["dimension_limit"], volume_change_limit=settings["volume_limit"])
        if not np.any(caps > 0):
            stats.update(moved_vertices=0, max_displacement=0., p95_displacement=0., dimension_change_ratio=0.)
            return finish("unchanged", "所有顶点都属于受保护边界或明显尖角，没有可安全平滑的区域；原文件已保留。")
        # Reserve headroom for float32 rounding; the public cap still applies
        # strictly to the actual exported coordinates, not just the work array.
        candidate = _taubin(normalized, edges, protected, caps * .98, settings["iterations"], deadline)
        if not np.any(np.linalg.norm(candidate - normalized, axis=1) > 1e-12):
            stats.update(moved_vertices=0, max_displacement=0., p95_displacement=0., dimension_change_ratio=0.)
            return finish("unchanged", "未检测到有效几何移动；仅增加面数不会变圆，未生成重复副本。")
        coordinates = origin + candidate * scale
        unchanged = protected | (np.linalg.norm(candidate - normalized, axis=1) < 1e-14)
        coordinates[unchanged] = vertices[unchanged]
        # Checks use the exact float32 values that will appear in the binary STL.
        exported = coordinates.astype(np.float32).astype(np.float64)
        if not np.isfinite(exported).all():
            raise _Rejected("平滑结果超出 STL 坐标范围，已取消输出。")
        if not np.array_equal(exported[protected], vertices[protected]):
            raise _Rejected("受保护顶点无法无损转为二进制 STL，已取消平滑以保留边界和 ASCII 坐标精度。")
        quantization = np.linalg.norm(exported - coordinates, axis=1) / scale
        if np.any(quantization > np.maximum(caps * .01, 1e-10)):
            raise _Rejected("二进制 STL 坐标量化误差过大，已取消平滑；请使用桌面工具保留精度。")
        actual = (exported - origin) / scale
        distance = np.linalg.norm(exported - vertices, axis=1)
        if np.any(distance / scale > caps + 1e-12):
            raise _Rejected("导出后的累计顶点位移超过保护上限，已取消副本。")
        moved = distance > scale * 1e-12
        stats.update(moved_vertices=int(moved.sum()), max_displacement=float(distance.max()),
                     p95_displacement=float(np.percentile(distance[moved], 95)) if moved.any() else 0.)
        if not moved.any():
            stats["dimension_change_ratio"] = 0.
            return finish("unchanged", "未检测到有效几何移动；仅增加面数不会变圆，未生成重复副本。")
        dimension_ratio = np.abs(np.ptp(exported, axis=0) - dimensions) / np.maximum(dimensions, scale * .001)
        stats["dimension_change_ratio"] = float(dimension_ratio.max())
        if stats["dimension_change_ratio"] > settings["dimension_limit"]:
            raise _Rejected("平滑副本的尺寸变化超出限制，已取消输出；可改用轻度或关闭细分。")
        previous = normalized[faces]
        new = actual[faces]
        normal_before = np.cross(previous[:, 1] - previous[:, 0], previous[:, 2] - previous[:, 0])
        normal_after = np.cross(new[:, 1] - new[:, 0], new[:, 2] - new[:, 0])
        alignment = np.einsum("ij,ij->i", normal_before, normal_after)
        area_ratio = np.linalg.norm(normal_after, axis=1) / np.linalg.norm(normal_before, axis=1)
        if np.any(alignment <= 0) or np.any(area_ratio < .1) or np.any(area_ratio > 10):
            raise _Rejected("平滑后出现面反转、面塌缩或异常拉伸，已取消输出。")
        if np.any(closed_faces):
            volume_ratio = _volume_ratios(normalized, actual, faces[closed_faces], face_labels[closed_faces])
            stats["volume_change_ratio"] = volume_ratio
            if volume_ratio is None:
                raise _Rejected("闭合网格的体积计算不可靠，无法安全验证平滑副本。")
            if volume_ratio > settings["volume_limit"]:
                raise _Rejected("平滑后闭合部件的体积变化超过限制，已取消输出。")
        # Avoid keeping several triangle-sized scratch arrays alive while the
        # independent parser/topology inspector verifies the actual export.
        del previous, new, normal_before, normal_after, alignment, area_ratio
        del candidate, coordinates, quantization, local, lengths, caps, normalized, actual, edges, face_labels, closed_faces
        mesh._tick(deadline)
        output = mesh._encode(exported[faces])
        output = b"Image3D verified geometry smoothing".ljust(80, b"\0") + output[80:]
        mesh._tick(deadline)
        after, checked_triangles, _ = mesh._inspect(output, deadline)
        after["sha256"] = hashlib.sha256(output).hexdigest()
        report["after"] = after
        after_stats = after["stats"]
        if (any(after_stats[key] for key in ("degenerate_faces", "duplicate_faces", "nonmanifold_edges", "inconsistent_edges"))
                or after_stats["faces"] != len(faces)
                or after_stats["components"] != before["stats"]["components"]
                or after_stats["boundary_edges"] != before["stats"]["boundary_edges"] * (2 if subdivide else 1)
                or not np.array_equal(checked_triangles, exported[faces])
                or len(np.unique(exported, axis=0)) != len(vertices)):
            raise _Rejected("实际导出的 STL 未通过拓扑复检，可能出现破面、顶点合并或部件连接变化；未交付副本。")
        mesh._tick(deadline)
        return finish("smoothed", "已生成并复检独立平滑副本；请对比细节和尺寸，原始 STL 保持不变。", output)
    except _Rejected as exc:
        return finish("rejected", str(exc))
    except mesh.CheckLimit as exc:
        return finish("incomplete", str(exc))
    except ValueError as exc:
        return finish("invalid", str(exc))
    except MemoryError:
        return finish("incomplete", "内存不足，平滑未完成；原文件已保留，未交付副本。")
    finally:
        mesh._SLOT.release()
