"""Bounded fallback reconstruction for STL surfaces that cannot be locally repaired.

Each input edge-connected component gets its own voxel grid.  Surface samples
are generated in bounded batches rather than recursively subdividing triangles.
No component is selected by size or discarded.  Reconstruction changes geometry
and can thicken sheets or fill cavities; the original bytes are never replaced.
"""
from __future__ import annotations

import hashlib
import time

import numpy as np

from . import mesh_quality as mesh
from .mesh_smoothing import _Rejected as _InvalidVertexFans
from .mesh_smoothing import _edges, _labels, _validate_vertex_fans

RECONSTRUCTION_VERSION = "stl-voxel-v1"
RECONSTRUCTION_SECONDS = 90
MAX_RECONSTRUCTION_BYTES = 64 * 1024 * 1024
MAX_RECONSTRUCTION_FACES = 1_000_000
MAX_GRID_CELLS = 8_000_000
MAX_COMPONENTS = 512
MAX_SURFACE_SAMPLES = 16_000_000
SAMPLE_BATCH = 200_000
# At most twelve Lewiner triangles per active cell; cap the extraction's
# temporary allocations independently of the final one-million-face limit.
MAX_ACTIVE_CELLS = 250_000
ATTEMPT_SETTINGS = ((160, 0), (160, 1), (128, 1), (96, 2), (72, 2), (56, 2), (40, 2))
LIMITATIONS = [
    "这是体素重建，会改变网格与细节；开放薄片会加厚，小间隙或内部空腔可能被填合。原始 STL 保留。",
    "每个原始按边连接的部件分别重建，不删除小部件，不将独立部件合并；请核对装配间隙和细节。",
    "已复检实际导出文件的闭合边、流形边、顶点面扇和面朝向；未证明无自相交、壁厚合适或可直接打印。STL 不包含单位。",
]


class _Retry(Exception):
    """This resolution is unsuitable; another bounded attempt may succeed."""


def _face_components(triangles: np.ndarray, deadline: float) -> tuple[np.ndarray, np.ndarray]:
    """Group all shared-edge occurrences, including non-manifold edges."""
    _, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3).astype(np.int32)
    edges = faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    edges.sort(axis=1)
    keys = (edges[:, 0].astype(np.uint64) << np.uint64(32)) | edges[:, 1].astype(np.uint64)
    order = np.argsort(keys)
    adjacent = np.flatnonzero(keys[order[1:]] == keys[order[:-1]])
    pairs = np.column_stack((order[adjacent] // 3, order[adjacent + 1] // 3)).astype(np.int32)
    labels = _labels(len(faces), pairs, deadline)
    _, labels, counts = np.unique(labels, return_inverse=True, return_counts=True)
    mesh._tick(deadline)
    return labels, counts


def _surface_grid(triangles: np.ndarray, pitch: float, deadline: float,
                  sample_budget: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Rasterize a dense barycentric net; bounded dilation seals the sample net.

    Adjacent barycentric samples are at most half a voxel apart.  A one-voxel
    dilation, performed by the caller, yields a conservative thickened surface.
    A triangle's sampling order never creates a recursively expanded mesh.
    """
    low = triangles.min(axis=(0, 1))
    high = triangles.max(axis=(0, 1))
    padding = 5
    origin = low - padding * pitch
    shape = np.ceil((high - low) / pitch).astype(np.int64) + padding * 2 + 1
    if np.prod(shape.astype(object)) > MAX_GRID_CELLS:
        raise _Retry("体素网格超过内存预算，降低分辨率后重试。")
    local = (triangles - origin) / pitch
    max_edge = np.maximum.reduce([np.linalg.norm(local[:, 1] - local[:, 0], axis=1),
                                  np.linalg.norm(local[:, 2] - local[:, 1], axis=1),
                                  np.linalg.norm(local[:, 0] - local[:, 2], axis=1)])
    steps = np.maximum(1, np.ceil(max_edge * 2).astype(np.int64))
    costs = (steps + 1) * (steps + 2) // 2
    samples = int(costs.sum())
    if samples > sample_budget:
        raise _Retry("表面采样超过预算，降低分辨率后重试；未丢弃三角面。")
    surface = np.zeros(tuple(shape), dtype=bool)
    # Group equal sampling densities so a million tiny input triangles do not
    # require a million Python loops.  Both faces and samples are batched.
    order = np.argsort(steps, kind="stable")
    groups = np.r_[0, np.flatnonzero(steps[order[1:]] != steps[order[:-1]]) + 1, len(order)]
    for start, end in zip(groups[:-1], groups[1:]):
        mesh._tick(deadline)
        n = int(steps[order[start]])
        cost = (n + 1) * (n + 2) // 2
        # Very large individual triangles still have bounded coordinate arrays.
        if cost > SAMPLE_BATCH:
            for index in order[start:end]:
                triangle = local[index]
                for row in range(n + 1):
                    if row % 64 == 0:
                        mesh._tick(deadline)
                    u = row / n
                    v = np.arange(n - row + 1, dtype=float) / n
                    points = triangle[0] + u * (triangle[1] - triangle[0]) + v[:, None] * (triangle[2] - triangle[0])
                    indices = np.rint(points).astype(np.intp)
                    surface[indices[:, 0], indices[:, 1], indices[:, 2]] = True
            continue
        rows = np.repeat(np.arange(n + 1), np.arange(n + 1, 0, -1))
        offsets = np.r_[0, np.cumsum(np.arange(n + 1, 1, -1))]
        cols = np.arange(cost) - np.repeat(offsets, np.arange(n + 1, 0, -1))
        weights = np.column_stack((rows, cols)).astype(float) / n
        batch_faces = max(1, SAMPLE_BATCH // cost)
        for offset in range(start, end, batch_faces):
            mesh._tick(deadline)
            group = local[order[offset:min(end, offset + batch_faces)]]
            points = (group[:, None, 0] + weights[None, :, :1] * (group[:, None, 1] - group[:, None, 0])
                      + weights[None, :, 1:] * (group[:, None, 2] - group[:, None, 0]))
            indices = np.rint(points).astype(np.intp).reshape(-1, 3)
            surface[indices[:, 0], indices[:, 1], indices[:, 2]] = True
    mesh._tick(deadline)
    return surface, origin, samples


def _active_cell_count(volume: np.ndarray) -> int:
    all_full = volume[:-1, :-1, :-1].copy()
    any_full = all_full.copy()
    for x, y, z in ((0, 0, 1), (0, 1, 0), (0, 1, 1), (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)):
        part = volume[x:volume.shape[0] - 1 + x,
                      y:volume.shape[1] - 1 + y,
                      z:volume.shape[2] - 1 + z]
        all_full &= part
        any_full |= part
    return int(np.count_nonzero(any_full & ~all_full))


def _reconstruct_component(triangles: np.ndarray, pitch: float, closing: int,
                           deadline: float, sample_budget: int) -> tuple[np.ndarray, dict]:
    from scipy import ndimage
    from skimage.measure import marching_cubes

    surface, origin, samples = _surface_grid(triangles, pitch, deadline, sample_budget)
    structure = ndimage.generate_binary_structure(3, 1)
    volume = ndimage.binary_dilation(surface, structure=structure, iterations=1)
    mesh._tick(deadline)
    if closing:
        volume = ndimage.binary_closing(volume, structure=structure, iterations=closing)
    volume = ndimage.binary_fill_holes(volume, structure=structure)
    mesh._tick(deadline)
    if not np.all(volume[surface]):
        raise _Retry("重建未覆盖全部原始表面采样，调整分辨率后重试。")
    active = _active_cell_count(volume)
    if active > MAX_ACTIVE_CELLS:
        raise _Retry("预计重建网格过大，降低分辨率后重试。")
    vertices, faces, _, _ = marching_cubes(volume.astype(np.float32), level=.5,
                                          spacing=(pitch, pitch, pitch),
                                          gradient_direction="descent", allow_degenerate=False,
                                          method="lewiner")
    mesh._tick(deadline)
    if len(faces) > MAX_RECONSTRUCTION_FACES:
        raise _Retry("重建输出超过面数上限，降低分辨率后重试。")
    vertices = np.asarray(vertices, dtype=np.float64) + origin
    result = vertices[faces]
    # The surface must remain in the same spatial envelope, allowing the
    # explicitly reported voxel thickening; no translation or rescaling occurs.
    before_low, before_high = triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))
    after_low, after_high = result.min(axis=(0, 1)), result.max(axis=(0, 1))
    allowance = pitch * 4.01
    if (np.any(after_low > before_low + pitch) or np.any(after_high < before_high - pitch)
            or np.any(before_low - after_low > allowance) or np.any(after_high - before_high > allowance)):
        raise _Retry("重建部件的空间范围变化超出体素容差，调整分辨率后重试。")
    return result, {"pitch": float(pitch), "grid_cells": int(volume.size),
                    "surface_samples": samples, "faces": len(faces),
                    "surface_coverage_fraction": 1.0,
                    "dimension_delta": ((after_high - after_low) - (before_high - before_low)).tolist()}


def process_reconstruction(raw: bytes) -> tuple[dict, bytes | None]:
    """Return a verified, separate closed reconstruction or an explicit failure."""
    start = time.monotonic()
    report = {"version": RECONSTRUCTION_VERSION, "source_sha256": hashlib.sha256(raw).hexdigest(),
              "status": "incomplete", "method": "voxel_reconstruction", "message": "",
              "attempts": [], "stats": {}, "before": None, "after": None,
              "limitations": list(LIMITATIONS), "elapsed_ms": 0}

    def finish(status: str, message: str, output: bytes | None = None):
        if report["attempts"] and report["attempts"][-1]["status"] == "running":
            report["attempts"][-1].update(status=status, message=message)
        report.update(status=status, message=message, elapsed_ms=round((time.monotonic() - start) * 1000))
        return report, output

    if len(raw) > MAX_RECONSTRUCTION_BYTES:
        return finish("incomplete", "文件超过 64 MiB 重建上限；原文件已保留。")
    if not mesh._SLOT.acquire(blocking=False):
        report["code"] = "MESH_BUSY"
        return finish("incomplete", "当前实例正在处理其他模型，请稍后重试；原文件已保留。")
    deadline = start + RECONSTRUCTION_SECONDS
    try:
        mesh._tick(deadline)
        before, triangles, _ = mesh._inspect(raw, deadline)
        before["sha256"] = report["source_sha256"]
        report["before"] = before
        labels, counts = _face_components(triangles, deadline)
        component_count = len(counts)
        if component_count > MAX_COMPONENTS:
            return finish("incomplete", f"共有 {component_count} 个独立部件，超过本次重建预算；没有删除任何小部件。")
        order = np.argsort(labels, kind="stable")
        ordered = triangles[order]
        del triangles, labels, order
        starts = np.r_[0, np.cumsum(counts)]
        components = [ordered[a:b] for a, b in zip(starts[:-1], starts[1:])]
        bounds = np.ptp(ordered.reshape(-1, 3), axis=0)
        span = float(bounds.max())
        if not np.isfinite(span) or span <= 0:
            raise ValueError("模型尺寸无效，无法重建。")
        report["stats"] = {"faces_before": len(ordered), "source_components": component_count,
                           "dimensions_before": bounds.tolist(), "details_changed": True}
        minimum_resolution = max(4, min(12, int(np.sqrt(MAX_RECONSTRUCTION_FACES / (component_count * 32)))))
        for resolution, closing in ATTEMPT_SETTINGS:
            mesh._tick(deadline)
            attempt = {"resolution": resolution, "closing_iterations": closing, "status": "running"}
            report["attempts"].append(attempt)
            try:
                parts, diagnostics = [], []
                samples, face_count = 0, 0
                for component in components:
                    mesh._tick(deadline)
                    component_span = float(np.ptp(component.reshape(-1, 3), axis=0).max())
                    # A tiny independent detail gets its own minimum grid instead
                    # of vanishing below the main model's global voxel pitch.
                    pitch = min(span / resolution, component_span / minimum_resolution)
                    result, details = _reconstruct_component(component, pitch, closing, deadline,
                                                              MAX_SURFACE_SAMPLES - samples)
                    samples += details["surface_samples"]
                    face_count += len(result)
                    if face_count > MAX_RECONSTRUCTION_FACES or 84 + face_count * 50 > MAX_RECONSTRUCTION_BYTES:
                        raise _Retry("全部部件合计超过输出预算，降低分辨率后重试；没有删除部件。")
                    parts.append(result)
                    diagnostics.append(details)
                reconstructed = np.concatenate(parts).astype(np.float32).astype(np.float64)
                del parts
                if not np.isfinite(reconstructed).all():
                    raise _Retry("重建坐标无法安全导出，调整分辨率后重试。")
                output = mesh._encode(reconstructed)
                output = b"Image3D independent-component voxel reconstruction".ljust(80, b"\0") + output[80:]
                mesh._tick(deadline)
                after, checked, _ = mesh._inspect(output, deadline)
                after["sha256"] = hashlib.sha256(output).hexdigest()
                stats = after["stats"]
                if (any(stats[key] for key in ("boundary_edges", "nonmanifold_edges", "inconsistent_edges",
                                               "degenerate_faces", "duplicate_faces"))
                        or not stats["edge_closed"] or stats["components"] != component_count
                        or not np.array_equal(checked, reconstructed)):
                    raise _Retry("实际导出的 STL 未通过闭合、面朝向或独立部件数量复检，调整体素参数后重试。")
                _, inverse = np.unique(checked.reshape(-1, 3), axis=0, return_inverse=True)
                checked_faces = inverse.reshape(-1, 3).astype(np.int32)
                try:
                    _validate_vertex_fans(checked_faces, _edges(checked_faces, deadline), deadline)
                except _InvalidVertexFans as exc:
                    raise _Retry("重建结果存在不连续顶点面扇，调整体素参数后重试。") from exc
                actual_dimensions = np.asarray(stats["dimensions"])
                pitches = [item["pitch"] for item in diagnostics]
                if np.any(np.abs(actual_dimensions - bounds) > max(pitches) * 8.02):
                    raise _Retry("实际导出模型的尺寸变化超出体素容差，调整分辨率后重试。")
                mesh._tick(deadline)
                report["after"] = after
                report["stats"].update(
                    faces_after=stats["faces"], output_components=stats["components"], components_preserved=True,
                    dimensions_after=stats["dimensions"], dimension_delta=(actual_dimensions - bounds).tolist(),
                    voxel_pitch_min=min(pitches), voxel_pitch_max=max(pitches),
                    surface_coverage_fraction=1.0, surface_samples=samples,
                    max_component_grid_cells=max(item["grid_cells"] for item in diagnostics),
                    resolution=resolution, closing_iterations=closing,
                    thickening_possible=True, max_voxel_envelope_expansion=max(pitches) * 4.01,
                )
                attempt.update(status="repaired", faces=stats["faces"], surface_samples=samples)
                return finish("repaired", "已逐部件完成体素重建并复检为闭合网格；没有删除小部件。薄片可能加厚、细节可能变化，请对比原版。", output)
            except (_Retry, ValueError) as exc:
                attempt.update(status="retry", message=str(exc))
        return finish("rejected", "已尝试多级体素分辨率与闭运算，仍未得到满足全部复检条件的副本；原文件已保留。")
    except mesh.CheckLimit as exc:
        if report["attempts"] and report["attempts"][-1]["status"] == "running":
            report["attempts"][-1].update(status="incomplete", message=str(exc))
        return finish("incomplete", "重建达到资源或时间上限；原文件已保留，未交付未验证副本。 " + str(exc))
    except ValueError as exc:
        return finish("invalid", str(exc))
    except ImportError:
        return finish("incomplete", "体素重建依赖尚未安装；原文件已保留。")
    except MemoryError:
        return finish("incomplete", "内存不足，体素重建已停止；原文件已保留。")
    finally:
        mesh._SLOT.release()
