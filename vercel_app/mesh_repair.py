"""Bounded local STL topology repair for the automatic finishing pipeline.

This stage never moves vertices or discards a component. Small conflicting
faces and small, simple, nearly planar holes can be repaired locally. Ambiguous
or larger damage is explicitly passed back to the pipeline for a different
repair method; it is never returned as a successful printable mesh.
"""
from __future__ import annotations

import hashlib
import heapq
import struct
import time

import numpy as np

from . import mesh_quality as mesh
from . import mesh_smoothing as smoothing

REPAIR_VERSION = "stl-local-repair-v2"
MAX_REPAIR_BYTES = 64 * 1024 * 1024
MAX_REPAIR_FACES = 1_000_000
REPAIR_SECONDS = 90
_REMOVED_AREA_RATIO = .005
_ADDED_AREA_RATIO = .01
_MAX_HOLE_VERTICES = 96
LIMITATIONS = [
    "局部修复仅处理小范围冲突面及简单小孔；复杂损坏需要升级处理方式。",
    "原文件保持不变；复检检查闭合、连接和朝向，不等于壁厚、自相交或可打印认证。",
]


class _NeedsFallback(Exception):
    pass


def _edge_data(faces: np.ndarray, deadline: float) -> dict:
    data = smoothing._edges(faces, deadline)
    ids = data["inverse"].ravel()
    order = np.argsort(ids, kind="stable")
    adjacent = ids[order[1:]] == ids[order[:-1]]
    pairs = np.column_stack((order[:-1][adjacent] // 3, order[1:][adjacent] // 3)).astype(np.int32)
    data["labels"] = smoothing._labels(len(faces), pairs, deadline)
    data["order"] = order
    data["starts"] = np.r_[0, np.cumsum(data["counts"][:-1])]
    return data


def _areas(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    triangles = vertices[faces]
    return np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2


def _remove_conflicts(vertices: np.ndarray, faces: np.ndarray, data: dict,
                      deadline: float) -> tuple[np.ndarray, np.ndarray, int]:
    """Remove the smallest local conflicting faces within each component budget."""
    incidence = data["counts"].copy()
    active = np.ones(len(faces), dtype=bool)
    area = _areas(vertices, faces)
    labels = data["labels"]
    budgets = np.bincount(labels, weights=area) * _REMOVED_AREA_RATIO
    spent = np.zeros_like(budgets)
    remaining = np.bincount(labels)
    edges = data["inverse"]
    bad = np.any(incidence[edges] > 2, axis=1)
    # Prefer removable fins (few regular edges) before disturbing a sheet.
    heap = [(int(np.count_nonzero(incidence[edges[i]] == 2)), float(area[i]), int(i)) for i in np.flatnonzero(bad)]
    heapq.heapify(heap)
    removed = 0
    maximum = min(10_000, max(1, int(len(faces) * .03)))
    while heap:
        if removed % 128 == 0:
            mesh._tick(deadline)
        _, _, face = heapq.heappop(heap)
        if not active[face] or not np.any(incidence[edges[face]] > 2):
            continue
        label = labels[face]
        if remaining[label] <= 4 or spent[label] + area[face] > budgets[label]:
            continue
        if removed >= maximum:
            raise _NeedsFallback("非流形冲突超过局部修复范围，需要更强的修复方式。")
        active[face] = False
        incidence[edges[face]] -= 1
        spent[label] += area[face]
        remaining[label] -= 1
        removed += 1
    if np.any(incidence > 2):
        raise _NeedsFallback("非流形冲突不能在保留细节的局部预算内消除，需要升级修复。")
    return faces[active], labels[active], removed


def _boundary_loops(faces: np.ndarray, data: dict, deadline: float) -> list[list[int]]:
    occurrences = np.flatnonzero(data["counts"][data["inverse"].ravel()] == 1)
    if not len(occurrences):
        return []
    edges = faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)[occurrences]
    if (len(np.unique(edges[:, 0])) != len(edges)
            or len(np.unique(edges[:, 1])) != len(edges)
            or not np.array_equal(np.sort(edges[:, 0]), np.sort(edges[:, 1]))):
        raise _NeedsFallback("边界存在分叉或不连续连接，需要升级修复。")
    successor = dict(map(tuple, edges.tolist()))
    loops = []
    while successor:
        mesh._tick(deadline)
        first = next(iter(successor))
        loop, current = [], first
        while True:
            loop.append(current)
            if len(loop) > _MAX_HOLE_VERTICES:
                raise _NeedsFallback("开放边界较大，需要升级修复。")
            current = successor.pop(current)
            if current == first:
                break
        if len(loop) < 3:
            raise _NeedsFallback("边界不足以形成有效补面，需要升级修复。")
        loops.append(loop)
    return loops


def _cross(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def _triangulate(vertices: np.ndarray, loop: list[int], diameter_limit: float) -> np.ndarray:
    """Ear clipping of a verified simple, nearly planar boundary polygon."""
    # Boundary direction belongs to the existing face; the cap must oppose it.
    ring = np.asarray(loop[::-1], dtype=np.int32)
    coordinates = vertices[ring]
    extent = float(np.linalg.norm(np.ptp(coordinates, axis=0)))
    if extent <= 0 or extent > diameter_limit:
        raise _NeedsFallback("孔洞跨度超出局部补面范围，需要升级修复。")
    centered = coordinates - coordinates.mean(axis=0)
    _, _, axes = np.linalg.svd(centered, full_matrices=False)
    if float(np.max(np.abs(centered @ axes[2]))) > extent * .03:
        raise _NeedsFallback("孔洞边界弯曲明显，无法可靠直接补面，需要升级修复。")
    points = centered @ axes[:2].T / extent
    tolerance = 1e-12
    # Reject crossing or touching non-adjacent projected edges before clipping.
    for i in range(len(points)):
        a, b = points[i], points[(i + 1) % len(points)]
        for j in range(i + 2, len(points)):
            if (j + 1) % len(points) == i:
                continue
            c, d = points[j], points[(j + 1) % len(points)]
            signs = (_cross(b - a, c - a), _cross(b - a, d - a),
                     _cross(d - c, a - c), _cross(d - c, b - c))
            if (signs[0] * signs[1] <= tolerance and signs[2] * signs[3] <= tolerance
                    and np.all(np.maximum(np.minimum(a, b), np.minimum(c, d))
                               <= np.minimum(np.maximum(a, b), np.maximum(c, d)) + tolerance)):
                raise _NeedsFallback("孔洞边界投影交叉，需升级修复。")
    signed = sum(_cross(points[i], points[(i + 1) % len(points)]) for i in range(len(points)))
    if abs(signed) <= tolerance:
        raise _NeedsFallback("孔洞投影面积不可靠，需要升级修复。")
    direction = 1 if signed > 0 else -1
    indices = list(range(len(ring)))
    triangles = []
    while len(indices) > 3:
        clipped = False
        for offset, middle in enumerate(indices):
            left, right = indices[offset - 1], indices[(offset + 1) % len(indices)]
            a, b, c = points[[left, middle, right]]
            if direction * _cross(b - a, c - b) <= tolerance:
                continue
            others = [item for item in indices if item not in (left, middle, right)]
            if any(min(direction * _cross(b - a, points[p] - a),
                       direction * _cross(c - b, points[p] - b),
                       direction * _cross(a - c, points[p] - c)) >= -tolerance for p in others):
                continue
            triangles.append(ring[[left, middle, right]])
            del indices[offset]
            clipped = True
            break
        if not clipped:
            raise _NeedsFallback("孔洞无法可靠三角化，需要升级修复。")
    triangles.append(ring[indices])
    return np.asarray(triangles, dtype=np.int32)


def process_repair(raw: bytes) -> tuple[dict, bytes | None]:
    """Return closed, manifold local output or a report asking for a fallback."""
    start = time.monotonic()
    report = {"version": REPAIR_VERSION, "source_sha256": hashlib.sha256(raw).hexdigest(),
              "status": "incomplete", "message": "", "stats": {}, "attempts": [],
              "before": None, "after": None, "limitations": list(LIMITATIONS), "elapsed_ms": 0}

    def finish(status, message, output=None):
        report.update(status=status, message=message, elapsed_ms=round((time.monotonic() - start) * 1000))
        return report, output

    if len(raw) > MAX_REPAIR_BYTES:
        return finish("incomplete", "文件超过本地修复大小上限，需要升级处理。")
    if not mesh._SLOT.acquire(blocking=False):
        report["code"] = "MESH_BUSY"
        return finish("incomplete", "当前实例正在处理其他模型，请稍后重试。")
    deadline = start + REPAIR_SECONDS
    stage = "cleanup"
    try:
        if len(raw) >= 84 and 84 + struct.unpack_from("<I", raw, 80)[0] * 50 == len(raw):
            if struct.unpack_from("<I", raw, 80)[0] > MAX_REPAIR_FACES:
                raise mesh.CheckLimit("三角形超过本地修复上限，需要升级处理。")
        before, triangles, flips = mesh._inspect(raw, deadline)
        before["sha256"] = report["source_sha256"]
        report["before"] = before
        stats = report["stats"]
        stats.update(faces_before=before["stats"]["faces"], removed_duplicate_faces=before["stats"]["duplicate_faces"],
                     removed_degenerate_faces=before["stats"]["degenerate_faces"], removed_conflict_faces=0,
                     filled_holes=0, added_faces=0, flipped_faces=int(flips.sum()))
        triangles[flips] = triangles[flips][:, [0, 2, 1]]
        vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
        faces = inverse.reshape(-1, 3).astype(np.int32)
        if len(faces) > MAX_REPAIR_FACES:
            raise mesh.CheckLimit("三角形超过本地修复上限，需要升级处理。")
        del triangles, inverse
        data = _edge_data(faces, deadline)
        original_labels = data["labels"]
        component_area = np.bincount(original_labels, weights=_areas(vertices, faces))
        component_min = np.full((len(component_area), 3), np.inf)
        component_max = np.full((len(component_area), 3), -np.inf)
        corner_labels = np.repeat(original_labels, 3)
        corners = vertices[faces].reshape(-1, 3)
        np.minimum.at(component_min, corner_labels, corners)
        np.maximum.at(component_max, corner_labels, corners)
        component_limits = np.linalg.norm(component_max - component_min, axis=1) * .08
        del component_min, component_max, corner_labels, corners
        changed = bool(stats["removed_duplicate_faces"] or stats["removed_degenerate_faces"] or flips.any())
        report["attempts"].append({"method": "cleanup", "status": "completed", "message": "已清理重复面、零面积面并检查朝向。"})
        if np.any(data["counts"] > 2):
            stage = "local_topology"
            faces, original_labels, removed = _remove_conflicts(vertices, faces, data, deadline)
            stats["removed_conflict_faces"] = removed
            changed = changed or bool(removed)
            topology, flips, orientable = mesh._topology(faces, deadline)
            if not orientable:
                raise _NeedsFallback("局部清理后网格仍不可定向，需要升级修复。")
            stats["flipped_faces"] += int(flips.sum())
            faces[flips] = faces[flips][:, [0, 2, 1]]
            data = _edge_data(faces, deadline)
        elif before["stats"]["inconsistent_edges"] and not stats["flipped_faces"]:
            raise _NeedsFallback("网格不可定向，需要升级修复。")
        if np.any(data["counts"] == 1):
            stage = "local_topology"
            loops = _boundary_loops(faces, data, deadline)
            vertex_label = {}
            for index in np.flatnonzero(np.any(data["counts"][data["inverse"]] == 1, axis=1)):
                for vertex in faces[index]:
                    vertex_label[int(vertex)] = int(original_labels[index])
            additions, added_labels = [], []
            spent = np.zeros_like(component_area)
            for loop in loops:
                mesh._tick(deadline)
                label = vertex_label[loop[0]]
                cap = _triangulate(vertices, loop, component_limits[label])
                spent[label] += float(_areas(vertices, cap).sum())
                if spent[label] > component_area[label] * _ADDED_AREA_RATIO:
                    raise _NeedsFallback("补面面积超出局部修复范围，需要升级修复。")
                additions.append(cap)
                added_labels.extend([label] * len(cap))
            faces = np.concatenate([faces, *additions])
            original_labels = np.r_[original_labels, np.asarray(added_labels, dtype=np.int32)]
            stats.update(filled_holes=len(loops), added_faces=sum(map(len, additions)))
            changed = changed or bool(additions)
        if len(faces) > MAX_REPAIR_FACES or 84 + len(faces) * 50 > MAX_REPAIR_BYTES:
            raise mesh.CheckLimit("补面后超过本地修复资源上限，需要升级处理。")
        if stage == "local_topology":
            report["attempts"].append({"method": stage, "status": "completed", "message": "已处理局部非流形冲突和简单小孔。"})
        stage = "validation"
        output_triangles = vertices[faces]
        if changed and not np.array_equal(output_triangles, output_triangles.astype(np.float32).astype(np.float64)):
            raise _NeedsFallback("原始坐标不能无损导出为二进制 STL，需要升级保精度修复。")
        output = mesh._encode(output_triangles) if changed else raw
        if changed:
            output = b"Image3D verified local topology repair".ljust(80, b"\0") + output[80:]
        after, checked, _ = mesh._inspect(output, deadline)
        after["sha256"] = hashlib.sha256(output).hexdigest()
        report["after"] = after
        after_stats = after["stats"]
        if not np.array_equal(checked, output_triangles):
            raise _NeedsFallback("导出复检发现网格与已验证几何不一致，需要升级修复。")
        if any(after_stats[key] for key in ("degenerate_faces", "duplicate_faces", "boundary_edges", "nonmanifold_edges", "inconsistent_edges")):
            raise _NeedsFallback("导出复检仍有结构缺陷，需要升级修复。")
        if after_stats["components"] != before["stats"]["components"]:
            raise _NeedsFallback("局部修复改变了部件连接，需换用保留部件的修复方式。")
        if len(np.unique(original_labels)) != before["stats"]["components"]:
            raise _NeedsFallback("局部修复无法保留每个原始部件，需要升级修复。")
        scale = float(np.linalg.norm(before["stats"]["dimensions"]))
        delta = np.abs(np.asarray(after_stats["dimensions"]) - before["stats"]["dimensions"])
        stats["dimension_change_ratio"] = float(delta.max() / max(scale, np.finfo(float).tiny))
        if stats["dimension_change_ratio"] > .005:
            raise _NeedsFallback("局部修复的尺寸变化超出范围，需要升级修复。")
        _, checked_inverse = np.unique(checked.reshape(-1, 3), axis=0, return_inverse=True)
        checked_faces = checked_inverse.reshape(-1, 3).astype(np.int32)
        smoothing._validate_vertex_fans(checked_faces, smoothing._edges(checked_faces, deadline), deadline)
        stats["faces_after"] = after_stats["faces"]
        report["attempts"].append({"method": "validation", "status": "completed", "message": "实际 STL 已通过闭合、连接和朝向复检。"})
        mesh._tick(deadline)
        return finish("repaired" if changed else "unchanged", "结构修复已完成。" if changed else "结构检查完成，无需修复。", output)
    except (_NeedsFallback, smoothing._Rejected) as exc:
        report["attempts"].append({"method": stage, "status": "rejected", "message": str(exc)})
        return finish("rejected", str(exc))
    except mesh.CheckLimit as exc:
        return finish("incomplete", str(exc))
    except ValueError as exc:
        return finish("invalid", str(exc))
    except MemoryError:
        return finish("incomplete", "内存不足，需要升级处理。")
    finally:
        mesh._SLOT.release()


def _preserving_conflicts(vertices: np.ndarray, faces: np.ndarray, data: dict,
                          deadline: float) -> np.ndarray:
    """Remove small overused-edge faces without discarding or splitting a part.

    An overused edge must retain faces in both directions. Arbitrarily keeping
    the two largest faces can create a non-orientable surface. Any deletions
    that disconnect an original part are restored before the candidate is used.
    """
    counts = data["counts"].copy()
    edges = data["inverse"]
    directions = data["forward"].reshape(-1, 3)
    forward = np.bincount(edges.ravel(), weights=directions.ravel(), minlength=len(counts)).astype(np.int32)
    active = np.ones(len(faces), dtype=bool)
    area = _areas(vertices, faces)
    labels = data["labels"]
    budgets = np.bincount(labels, weights=area) * _REMOVED_AREA_RATIO
    spent = np.zeros_like(budgets)
    remaining = np.bincount(labels)
    maximum = min(10_000, max(1, int(len(faces) * .03)))
    candidates = np.flatnonzero(np.any(counts[edges] > 2, axis=1))
    queue = [(int(np.count_nonzero(counts[edges[i]] == 2)), float(area[i]), int(i)) for i in candidates]
    heapq.heapify(queue)
    removed = 0
    while queue and removed < maximum:
        if removed % 128 == 0:
            mesh._tick(deadline)
        _, _, index = heapq.heappop(queue)
        edge_ids = edges[index]
        overused = counts[edge_ids] > 2
        if not np.any(overused):
            continue
        same_direction = np.where(directions[index], forward[edge_ids], counts[edge_ids] - forward[edge_ids])
        label = labels[index]
        if (np.any(overused & (same_direction < 2)) or remaining[label] <= 4
                or spent[label] + area[index] > budgets[label]):
            continue
        active[index] = False
        counts[edge_ids] -= 1
        forward[edge_ids] -= directions[index]
        spent[label] += area[index]
        remaining[label] -= 1
        removed += 1
    if not removed:
        return active
    # Deleted junctions sometimes hold a small detail onto its original part.
    # Restore the local junction faces instead of silently detaching that detail.
    for _ in range(16):
        mesh._tick(deadline)
        kept_ids = np.flatnonzero(active)
        kept_labels = _edge_data(faces[active], deadline)["labels"]
        original_labels = labels[active]
        groups, sizes = np.unique(np.column_stack((original_labels, kept_labels)), axis=0, return_counts=True)
        order = np.lexsort((-sizes, groups[:, 0]))
        ranked = groups[order]
        first = np.r_[True, ranked[1:, 0] != ranked[:-1, 0]]
        largest = np.full(len(faces), -1, dtype=np.int32)
        largest[ranked[first, 0]] = ranked[first, 1]
        small = kept_labels != largest[original_labels]
        if not np.any(small):
            return active
        touched_edges = np.unique(edges[kept_ids[small]])
        restore = ~active & np.any(np.isin(edges, touched_edges), axis=1)
        if not np.any(restore):
            break
        active[restore] = True
    # If reconnection cannot be established within a bounded local search,
    # retain all faces. Other independent cleanup steps can still proceed.
    return np.ones(len(faces), dtype=bool)


def _local_orientation(faces: np.ndarray, data: dict, deadline: float) -> np.ndarray:
    """Orient consistent manifold face regions, leaving contradictory regions."""
    occurrences = data["occurrences"]
    pairs = occurrences // 3
    required = data["forward"][occurrences[:, 0]] == data["forward"][occurrences[:, 1]]
    parent = np.arange(len(faces), dtype=np.int32)
    rank = np.zeros(len(faces), dtype=np.uint8)
    parity = np.zeros(len(faces), dtype=np.uint8)
    contradictions = set()

    def root(node):
        bit = 0
        while parent[node] != node:
            bit ^= int(parity[node])
            node = int(parent[node])
        return node, bit

    for index, (left, right) in enumerate(pairs):
        if index % 2048 == 0:
            mesh._tick(deadline)
        a, pa = root(int(left))
        b, pb = root(int(right))
        difference = int(required[index]) ^ pa ^ pb
        if a == b:
            if difference:
                contradictions.add(a)
            continue
        if rank[a] < rank[b]:
            a, b = b, a
        parent[b], parity[b] = a, difference
        if rank[a] == rank[b]:
            rank[a] += 1
    roots = np.empty(len(faces), dtype=np.int32)
    flips = np.zeros(len(faces), dtype=bool)
    for index in range(len(faces)):
        if index % 2048 == 0:
            mesh._tick(deadline)
        roots[index], flips[index] = root(index)
    sizes = np.bincount(roots, minlength=len(faces))
    flipped = np.bincount(roots[flips], minlength=len(faces))
    flips ^= (flipped * 2 > sizes)[roots]
    if contradictions:
        flips[np.isin(roots, [root(item)[0] for item in contradictions])] = False
    return flips


def _fan_defects(faces: np.ndarray, data: dict, deadline: float) -> int:
    occurrences = data["occurrences"]
    low = np.where(data["forward"][occurrences], occurrences,
                   occurrences // 3 * 3 + (occurrences + 1) % 3)
    high = np.where(data["forward"][occurrences], occurrences // 3 * 3 + (occurrences + 1) % 3, occurrences)
    labels = smoothing._labels(faces.size, np.concatenate((low, high)), deadline)
    count = int(faces.max()) + 1
    smallest = np.full(count, faces.size, dtype=np.int32)
    largest = np.zeros(count, dtype=np.int32)
    np.minimum.at(smallest, faces.ravel(), labels)
    np.maximum.at(largest, faces.ravel(), labels)
    used = np.bincount(faces.ravel(), minlength=count) > 0
    return int(np.count_nonzero(used & (smallest != largest)))


def _simple_local_caps(vertices: np.ndarray, faces: np.ndarray, data: dict,
                       original_labels: np.ndarray, deadline: float) -> tuple[np.ndarray, int, int]:
    """Fill independent simple holes; one ambiguous hole does not block others."""
    occurrences = np.flatnonzero(data["counts"][data["inverse"].ravel()] == 1)
    boundary = faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)[occurrences]
    if not len(boundary):
        return np.empty((0, 3), dtype=np.int32), 0, 0
    boundary_labels = smoothing._labels(len(vertices), boundary, deadline)
    component_area = np.bincount(original_labels, weights=_areas(vertices, faces))
    spent = np.zeros_like(component_area)
    # These maps are limited to boundary vertices/edges, rather than whole-mesh
    # Python objects. New cap diagonals may not reuse an existing mesh edge.
    vertex_component = {int(vertex): int(original_labels[index])
                        for index in np.unique(occurrences // 3) for vertex in faces[index]}
    edge_keys = (data["vertices"][:, 0].astype(np.uint64) << np.uint64(32)) | data["vertices"][:, 1].astype(np.uint64)
    existing_edges = set(map(int, edge_keys))
    extent = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    caps, filled, skipped = [], 0, 0
    labels = boundary_labels[boundary[:, 0]]
    order = np.argsort(labels, kind="stable")
    groups = np.r_[0, np.flatnonzero(labels[order[1:]] != labels[order[:-1]]) + 1, len(order)]
    for start, end in zip(groups[:-1], groups[1:]):
        mesh._tick(deadline)
        edges = boundary[order[start:end]]
        if (len(edges) > _MAX_HOLE_VERTICES or len(np.unique(edges[:, 0])) != len(edges)
                or len(np.unique(edges[:, 1])) != len(edges)
                or not np.array_equal(np.sort(edges[:, 0]), np.sort(edges[:, 1]))):
            skipped += 1
            continue
        successor = dict(map(tuple, edges.tolist()))
        current = first = next(iter(successor))
        loop = []
        while current in successor:
            loop.append(current)
            current = successor.pop(current)
        if current != first or successor or len(loop) < 3:
            skipped += 1
            continue
        component = vertex_component[first]
        try:
            cap = _triangulate(vertices, loop, extent * .01)
        except _NeedsFallback:
            skipped += 1
            continue
        area = float(_areas(vertices, cap).sum())
        if spent[component] + area > component_area[component] * _ADDED_AREA_RATIO:
            skipped += 1
            continue
        cap_edges = cap[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
        cap_edges.sort(axis=1)
        keys = (cap_edges[:, 0].astype(np.uint64) << np.uint64(32)) | cap_edges[:, 1].astype(np.uint64)
        unique, counts = np.unique(keys, return_counts=True)
        # Boundary edges occur once in the cap; a diagonal occurs twice and
        # must be absent from all existing geometry (including earlier caps).
        if any(int(key) in existing_edges for key in unique[counts > 1]):
            skipped += 1
            continue
        # A single isolated triangle's boundary would create its duplicate.
        if len(cap) == 1 and np.any(np.all(np.sort(faces, axis=1) == np.sort(cap[0]), axis=1)):
            skipped += 1
            continue
        existing_edges.update(map(int, unique))
        spent[component] += area
        caps.append(cap)
        filled += 1
    return (np.concatenate(caps) if caps else np.empty((0, 3), dtype=np.int32)), filled, skipped


def process_preserving_repair(raw: bytes) -> tuple[dict, bytes | None]:
    """Improve local defects without moving any retained vertex or rebuilding.

    Remaining defects are reported as ``partial`` and are never represented as
    closure or printable certification. Even an irreparable isolated sheet is
    retained unchanged, so a valid original can still be offered to the user.
    """
    start = time.monotonic()
    report = {"version": REPAIR_VERSION, "method": "surface_preserving",
              "source_sha256": hashlib.sha256(raw).hexdigest(), "status": "incomplete",
              "message": "", "stats": {}, "before": None, "after": None,
              "attempts": [], "needs_review": True,
              "limitations": ["仅修改局部面连接，保留主体顶点和每个部件；未修复的问题会保留并标记。",
                              "未认证自相交、壁厚和可打印性；原始 STL 始终保留。"], "elapsed_ms": 0}

    def finish(status, message, output=None):
        report.update(status=status, message=message, elapsed_ms=round((time.monotonic() - start) * 1000))
        return report, output

    if len(raw) > MAX_REPAIR_BYTES:
        return finish("incomplete", "文件超过保形处理容量，原件已保留。")
    if not mesh._SLOT.acquire(blocking=False):
        report["code"] = "MESH_BUSY"
        return finish("incomplete", "当前实例正在处理其他模型，请稍后重试。")
    deadline = start + REPAIR_SECONDS
    try:
        before, triangles, _ = mesh._inspect(raw, deadline)
        before["sha256"] = report["source_sha256"]
        report["before"] = before
        vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
        original_faces = inverse.reshape(-1, 3).astype(np.int32)
        if len(original_faces) > MAX_REPAIR_FACES:
            raise mesh.CheckLimit("模型超过保形处理面数上限，原件已保留。")
        del triangles, inverse
        original_data = _edge_data(original_faces, deadline)
        before_fans = _fan_defects(original_faces, original_data, deadline)
        before["stats"]["nonmanifold_vertices"] = before_fans
        active = _preserving_conflicts(vertices, original_faces, original_data, deadline)
        selected = original_faces[active].copy()
        selected_data = _edge_data(selected, deadline)
        flips = _local_orientation(selected, selected_data, deadline)
        selected[flips] = selected[flips][:, [0, 2, 1]]
        selected_data = _edge_data(selected, deadline)
        caps, filled, skipped = _simple_local_caps(vertices, selected, selected_data,
                                                   original_data["labels"][active], deadline)
        cleanup = original_faces.copy()
        cleanup_flips = _local_orientation(cleanup, original_data, deadline)
        cleanup[cleanup_flips] = cleanup[cleanup_flips][:, [0, 2, 1]]
        candidates = [(np.concatenate((selected, caps)), int((~active).sum()), int(flips.sum()), filled, len(caps)),
                      (selected, int((~active).sum()), int(flips.sum()), 0, 0),
                      (cleanup, 0, int(cleanup_flips.sum()), 0, 0)]
        problems = ("degenerate_faces", "duplicate_faces", "boundary_edges", "nonmanifold_edges", "inconsistent_edges")
        for faces, removed, flipped, hole_count, added in candidates:
            mesh._tick(deadline)
            if len(faces) > MAX_REPAIR_FACES or 84 + len(faces) * 50 > MAX_REPAIR_BYTES:
                continue
            geometry = vertices[faces]
            changed = (removed or added or flipped or before["stats"]["duplicate_faces"] or before["stats"]["degenerate_faces"])
            if changed and not np.array_equal(geometry, geometry.astype(np.float32).astype(np.float64)):
                report["attempts"].append({"status": "skipped", "message": "局部修改不能无损导出，保留原坐标。"})
                continue
            output = mesh._encode(geometry) if changed else raw
            if changed:
                output = b"Image3D surface-preserving local repair".ljust(80, b"\0") + output[80:]
            after, checked, _ = mesh._inspect(output, deadline)
            after["sha256"] = hashlib.sha256(output).hexdigest()
            if (not np.array_equal(checked, geometry)
                    or any(after["stats"][key] > before["stats"][key] for key in problems)
                    or after["stats"]["components"] != before["stats"]["components"]):
                report["attempts"].append({"status": "skipped", "message": "候选未通过缺陷不增加及部件保留检查，已回退。"})
                continue
            _, check_inverse = np.unique(checked.reshape(-1, 3), axis=0, return_inverse=True)
            check_faces = check_inverse.reshape(-1, 3).astype(np.int32)
            fans = _fan_defects(check_faces, smoothing._edges(check_faces, deadline), deadline)
            after["stats"]["nonmanifold_vertices"] = fans
            scale = max(float(np.linalg.norm(before["stats"]["dimensions"])), np.finfo(float).tiny)
            dimension_ratio = float(np.max(np.abs(np.asarray(after["stats"]["dimensions"]) - before["stats"]["dimensions"])) / scale)
            if fans > before_fans or dimension_ratio > .005:
                report["attempts"].append({"status": "skipped", "message": "候选增加顶点连接问题或改变尺寸，已回退。"})
                continue
            report["after"] = after
            report["needs_review"] = bool(fans or any(after["stats"][key] for key in problems))
            report["stats"] = {"faces_before": before["stats"]["faces"], "faces_after": after["stats"]["faces"],
                               "removed_conflict_faces": removed, "added_faces": added, "flipped_faces": flipped,
                               "removed_duplicate_faces": before["stats"]["duplicate_faces"],
                               "removed_degenerate_faces": before["stats"]["degenerate_faces"],
                               "filled_holes": hole_count, "skipped_boundary_groups": skipped,
                               "moved_vertices": 0, "max_vertex_displacement": 0.,
                               "dimension_change_ratio": dimension_ratio, "components_preserved": True,
                               "defects_not_increased": True, "geometry_changed": bool(changed)}
            if report["needs_review"]:
                return finish("partial", "已完成保形局部处理；仍有结构问题需核对，未强行重建或补平。", output)
            return finish("repaired" if changed else "unchanged", "保形修复已完成。" if changed else "结构检查完成，无需修复。", output)
        # All attempted edits were unsuitable. The inspected original remains a
        # valid deliverable, with its existing defects explicitly disclosed.
        report["after"] = {**before, "sha256": report["source_sha256"]}
        report["stats"] = {"faces_before": before["stats"]["faces"], "faces_after": before["stats"]["faces"],
                           "moved_vertices": 0, "max_vertex_displacement": 0., "geometry_changed": False,
                           "components_preserved": True, "defects_not_increased": True, "dimension_change_ratio": 0.}
        return finish("partial", "局部修复未找到更安全的结果，已保留原几何；仍有结构问题需核对。", raw)
    except mesh.CheckLimit as exc:
        return finish("incomplete", str(exc))
    except ValueError as exc:
        return finish("invalid", str(exc))
    except MemoryError:
        return finish("incomplete", "内存不足，原件已保留。")
    finally:
        mesh._SLOT.release()
