"""One bounded step of the resumable automatic STL repair/smoothing pipeline."""
from __future__ import annotations

import hashlib

from .mesh_smoothing import process_smoothing

PROCESSOR_VERSION = "stl-auto-v1"
MAX_PROCESS_BYTES = 64 * 1024 * 1024
STAGES = {"repair", "reconstruct", "smooth", "smooth_cautious", "smooth_minimal"}
_SMOOTHING = {"smooth": ("gentle", "smooth_cautious"),
              "smooth_cautious": ("cautious", "smooth_minimal"),
              "smooth_minimal": ("minimal", None)}
_STRUCTURAL_PROBLEMS = ("boundary_edges", "nonmanifold_edges", "inconsistent_edges",
                        "duplicate_faces", "degenerate_faces")


def _verified(report: dict, output: bytes, raw: bytes) -> bool:
    check = report.get("after") or (report.get("before") if output == raw else None) or {}
    stats = check.get("stats") or {}
    return (check.get("sha256") == hashlib.sha256(output).hexdigest()
            and stats.get("faces", 0) > 0
            and all(stats.get(key) == 0 for key in _STRUCTURAL_PROBLEMS))


def process_stage(raw: bytes, stage: str) -> tuple[dict, bytes | None, str | None]:
    """Return report, verified intermediate/final bytes, and the next stage.

    Each attempt gets its own request budget. A fallback always receives the
    original/repaired source, never a partially failed output.
    """
    if stage == "repair":
        from .mesh_repair import process_repair
        report, output = process_repair(raw)
        next_stage = "smooth" if output is not None else "reconstruct"
    elif stage == "reconstruct":
        from .mesh_reconstruction import process_reconstruction
        report, output = process_reconstruction(raw)
        next_stage = "smooth" if output is not None else None
    elif stage in _SMOOTHING:
        strength, fallback = _SMOOTHING[stage]
        report, output = process_smoothing(raw, strength=strength, subdivide=False)
        if report.get("status") == "unchanged":
            output = raw
        next_stage = "done" if output is not None else fallback
    else:
        raise ValueError("Unknown STL processing stage")
    digest = hashlib.sha256(raw).hexdigest()
    report["stage"] = stage
    if report.get("source_sha256") != digest:
        raise ValueError("STL stage source digest mismatch")
    if report.get("code") == "MESH_BUSY":
        # Another session owns the shared worker slot. No geometric attempt
        # took place, so retain this stage and let the caller retry it later.
        return report, None, stage
    if output is not None:
        if (not output or len(output) > MAX_PROCESS_BYTES
                or report.get("status") not in {"repaired", "smoothed", "unchanged"}
                or not _verified(report, output, raw)):
            # Never deliver this output; try the next method using the last
            # verified source. Source digest mismatches above remain fatal.
            report.update(status="rejected", code="STL_OUTPUT_UNVERIFIED",
                          message="输出未通过复检，尝试其他处理方式。")
            next_stage = "reconstruct" if stage == "repair" else _SMOOTHING.get(stage, (None, None))[1]
            return report, None, next_stage
    return report, output, next_stage
