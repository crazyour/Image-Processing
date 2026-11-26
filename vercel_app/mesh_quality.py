"""Bounded, CPU-only STL checks. Never weld gaps, fill holes or remove components.

Topology uses exact vertex coordinates and unique nonzero-area faces. Edge
incidence and winding follow https://trimesh.org/trimesh.html#trimesh.Trimesh.is_watertight
but no additional mesh runtime (or paid model) is required.
"""
from __future__ import annotations

import hashlib
import re
import struct
import threading
import time

import numpy as np

CHECKER_VERSION = "stl-basic-v1"
MAX_CHECK_BYTES = 64 * 1024 * 1024
MAX_CHECK_FACES = 1_000_000
CHECK_SECONDS = 45
_SLOT = threading.BoundedSemaphore(1)
_RECORD = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_TRIPLE = rf"({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})"
_FACET = re.compile(
    rf"\s*facet\s+normal\s+{_TRIPLE}\s+outer\s+loop\s+"
    rf"vertex\s+{_TRIPLE}\s+vertex\s+{_TRIPLE}\s+vertex\s+{_TRIPLE}"
    r"\s+endloop\s+endfacet", re.IGNORECASE,
)
LIMITATIONS = [
    "基础检查不等于可打印认证；未检查自相交、非流形顶点、壁厚、支撑和形状是否符合参考图。",
    "按完全相同的坐标连接顶点，不合并微小间隙；拓扑统计基于去除重复面及零面积面后的网格。",
    "多个部件和开放边界可能是设计意图；不会自动补洞、删除部件或判断内外朝向。STL 不包含单位。",
]


class CheckLimit(Exception):
    pass


def _tick(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise CheckLimit("达到检查时间上限，原文件已保留。")


def incomplete_report(reason: str, *, sha256: str | None = None) -> dict:
    return {"version": CHECKER_VERSION, "status": "incomplete", "sha256": sha256,
            "issues": [reason], "stats": {}, "repairable": False,
            "limitations": LIMITATIONS, "checked_at": int(time.time())}


def _parse(raw: bytes, deadline: float) -> np.ndarray:
    if len(raw) > MAX_CHECK_BYTES:
        raise CheckLimit("文件超过 64 MiB 基础检查上限，请使用桌面网格工具检查。")
    if len(raw) >= 84:
        count = struct.unpack_from("<I", raw, 80)[0]
        if 84 + count * 50 == len(raw):
            if count > MAX_CHECK_FACES:
                raise CheckLimit("三角形超过 100 万面检查上限，请使用桌面网格工具检查。")
            if not count:
                raise ValueError("STL 没有三角形。")
            return np.frombuffer(raw, dtype=_RECORD, offset=84, count=count)["vertices"].astype(np.float64)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("STL 文件结构或三角形数量不完整。") from exc
    header = re.match(r"\s*solid[^\r\n]*[\r\n]+", text, re.IGNORECASE)
    if not header:
        raise ValueError("不是完整的二进制或 ASCII STL。")
    # Preallocate bounded storage; don't build millions of Python float objects.
    count = len(re.findall(r"\bfacet\s+normal\b", text, re.IGNORECASE))
    if count > MAX_CHECK_FACES:
        raise CheckLimit("三角形超过 100 万面检查上限，请使用桌面网格工具检查。")
    if not count:
        raise ValueError("STL 没有三角形。")
    triangles = np.empty((count, 3, 3), dtype=np.float64)
    pos = header.end()
    for index in range(count):
        if index % 2048 == 0:
            _tick(deadline)
        match = _FACET.match(text, pos)
        if not match:
            raise ValueError("ASCII STL 三角形结构不完整。")
        triangles[index] = np.asarray(match.groups()[3:], dtype=np.float64).reshape(3, 3)
        pos = match.end()
    if not re.fullmatch(r"\s*endsolid[^\r\n]*\s*", text[pos:], re.IGNORECASE):
        raise ValueError("ASCII STL 缺少结束标记或包含无法识别的数据。")
    return triangles


def _topology(faces: np.ndarray, deadline: float) -> tuple[dict, np.ndarray, bool]:
    """Use sorted edge occurrences and parity union-find (no Python adjacency graph)."""
    count = len(faces)
    edges = faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    forward = edges[:, 0] < edges[:, 1]
    edges.sort(axis=1)
    # Vertex IDs are <= 3 million; pack an undirected edge into one uint64.
    keys = (edges[:, 0].astype(np.uint64) << np.uint64(32)) | edges[:, 1].astype(np.uint64)
    order = np.argsort(keys)
    ordered = keys[order]
    starts = np.r_[0, np.flatnonzero(ordered[1:] != ordered[:-1]) + 1]
    incidence = np.diff(np.r_[starts, len(order)])
    boundary = int(np.count_nonzero(incidence == 1))
    nonmanifold = int(np.count_nonzero(incidence > 2))
    pair_starts = starts[incidence == 2]
    winding = int(np.count_nonzero(forward[order[pair_starts]] == forward[order[pair_starts + 1]]))
    _tick(deadline)
    # Union all occurrences of the same edge, including non-manifold connections.
    adjacent = np.flatnonzero(ordered[1:] == ordered[:-1])
    left, right = order[adjacent], order[adjacent + 1]
    parity = forward[left] == forward[right]
    left, right = left // 3, right // 3
    parent = np.arange(count, dtype=np.int32)
    rank = np.zeros(count, dtype=np.uint8)
    xor = np.zeros(count, dtype=np.uint8)

    def root(node):
        bit = 0
        while parent[node] != node:
            bit ^= int(xor[node])
            node = int(parent[node])
        return node, bit

    components, orientable = count, True
    for i in range(len(left)):
        if i % 2048 == 0:
            _tick(deadline)
        a, pa = root(int(left[i]))
        b, pb = root(int(right[i]))
        required = int(parity[i]) ^ pa ^ pb
        if a == b:
            if required:
                orientable = False
            continue
        if rank[a] < rank[b]:
            a, b = b, a
        parent[b] = a
        xor[b] = required
        if rank[a] == rank[b]:
            rank[a] += 1
        components -= 1
    flips = np.zeros(count, dtype=bool)
    if orientable and not nonmanifold and winding:
        # Choose the smaller flip set per component; never infer outward normals.
        roots = np.empty(count, dtype=np.int32)
        for i in range(count):
            if i % 2048 == 0:
                _tick(deadline)
            roots[i], flips[i] = root(i)
        sizes = np.bincount(roots, minlength=count)
        flipped = np.bincount(roots[flips], minlength=count)
        flips ^= (flipped * 2 > sizes)[roots]
    return {"boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "inconsistent_edges": winding, "components": components,
            "edge_closed": boundary == 0 and nonmanifold == 0}, flips, orientable and not nonmanifold


def _inspect(raw: bytes, deadline: float) -> tuple[dict, np.ndarray, np.ndarray]:
    triangles = _parse(raw, deadline)
    if not np.isfinite(triangles).all():
        raise ValueError("STL 含 NaN 或无穷坐标，不能安全修复。")
    if np.max(np.abs(triangles)) > np.finfo(np.float32).max:
        raise ValueError("坐标超出二进制 STL 可表达范围。")
    stats = {"faces": len(triangles),
             "dimensions": np.ptp(triangles.reshape(-1, 3), axis=0).tolist()}
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    # Only exactly zero area, not a scale-dependent epsilon that erases fine detail.
    valid = np.any(normals != 0, axis=1)
    stats["degenerate_faces"] = int(np.count_nonzero(~valid))
    triangles = triangles[valid]
    if not len(triangles):
        raise ValueError("全部三角形面积为零，无法生成有效修复副本。")
    _tick(deadline)
    _, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3).astype(np.int32)
    _, first = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    first.sort()
    stats["duplicate_faces"] = len(faces) - len(first)
    triangles, faces = triangles[first], faces[first]
    stats["clean_faces"] = len(faces)
    _tick(deadline)
    topology, flips, orientable = _topology(faces, deadline)
    stats.update(topology)
    issues = []
    for key, label in (("degenerate_faces", "零面积面"), ("duplicate_faces", "重复面"),
                       ("boundary_edges", "开放边界边（不是孔洞数量）"),
                       ("nonmanifold_edges", "非流形边"), ("inconsistent_edges", "朝向不一致的公共边")):
        if stats[key]:
            issues.append(f"{label}：{stats[key]}。")
    if stats["components"] > 1:
        issues.append(f"检测到 {stats['components']} 个按边连接的部件，可能是正常零件，请人工核对。")
    if not orientable:
        issues.append("连接关系不支持安全统一朝向，将保留原朝向。")
    if not orientable:
        flips[:] = False
    stats["repair_flip_faces"] = int(np.count_nonzero(flips))
    report = {"version": CHECKER_VERSION, "status": "issues" if issues else "passed",
              "issues": issues, "stats": stats,
              "repairable": bool(stats["duplicate_faces"] or stats["degenerate_faces"] or flips.any()),
              "limitations": LIMITATIONS, "checked_at": int(time.time())}
    _tick(deadline)
    return report, triangles, flips


def _encode(triangles: np.ndarray) -> bytes:
    records = np.zeros(len(triangles), dtype=_RECORD)
    records["vertices"] = triangles
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    length = np.linalg.norm(normals, axis=1)
    records["normal"] = normals / length[:, None]
    return b"Image3D conservative STL cleanup".ljust(80, b"\0") + struct.pack("<I", len(triangles)) + records.tobytes()


def process_stl(raw: bytes, *, repair: bool = False) -> tuple[dict, bytes | None]:
    """Return a JSON report and, only on explicit repair, a verified separate copy."""
    digest = hashlib.sha256(raw).hexdigest()
    if not _SLOT.acquire(blocking=False):
        return incomplete_report("当前实例正在检查其他模型，请稍后重试。", sha256=digest), None
    start = time.monotonic()
    deadline = start + CHECK_SECONDS
    try:
        report, triangles, flips = _inspect(raw, deadline)
        report["sha256"] = digest
        output = None
        if repair and report["repairable"]:
            triangles[flips] = triangles[flips][:, [0, 2, 1]]
            # Avoid silently quantizing high-precision ASCII coordinates.
            if not np.array_equal(triangles, triangles.astype(np.float32).astype(np.float64)):
                report["repairable"] = False
                report["issues"].append("ASCII 坐标无法无损转为二进制 STL，已取消修复；请使用桌面工具保留精度。")
            else:
                output = _encode(triangles)
                _tick(deadline)
                after, _, _ = _inspect(output, deadline)
                if (after["stats"]["duplicate_faces"] or after["stats"]["degenerate_faces"]
                        or (flips.any() and after["stats"]["inconsistent_edges"])):
                    raise CheckLimit("修复副本未通过复检，未保存副本；原文件已保留。")
                after["sha256"] = hashlib.sha256(output).hexdigest()
                report["repair"] = {"removed_duplicate_faces": report["stats"]["duplicate_faces"],
                                    "removed_degenerate_faces": report["stats"]["degenerate_faces"],
                                    "flipped_faces": report["stats"]["repair_flip_faces"], "report": after}
        report["elapsed_ms"] = round((time.monotonic() - start) * 1000)
        return report, output
    except CheckLimit as exc:
        return incomplete_report(str(exc), sha256=digest), None
    except ValueError as exc:
        report = incomplete_report(str(exc), sha256=digest)
        report["status"] = "invalid"
        return report, None
    except MemoryError:
        return incomplete_report("内存不足，检查未完成；原文件已保留。", sha256=digest), None
    finally:
        _SLOT.release()
