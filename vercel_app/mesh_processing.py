"""One bounded step of the resumable automatic STL repair/smoothing pipeline."""
from __future__ import annotations

import hashlib

from .mesh_smoothing import process_smoothing

PROCESSOR_VERSION = "stl-auto-v2"
MAX_PROCESS_BYTES = 64 * 1024 * 1024
STAGES = {"repair", "preserve", "smooth", "smooth_cautious", "smooth_minimal"}
_SMOOTHING = {"smooth": ("gentle", "smooth_cautious"),
              "smooth_cautious": ("cautious", "smooth_minimal"),
              "smooth_minimal": ("minimal", None)}
_STRUCTURAL_PROBLEMS = ("boundary_edges", "nonmanifold_edges", "inconsistent_edges",
                        "duplicate_faces", "degenerate_faces")


def _verified(report: dict, output: bytes, raw: bytes) -> bool:
    check = report.get("after") or (report.get("before") if output == raw else None) or {}
    stats = check.get("stats") or {}
    if check.get("sha256") != hashlib.sha256(output).hexdigest() or stats.get("faces", 0) <= 0:
        return False
    if all(stats.get(key) == 0 for key in _STRUCTURAL_PROBLEMS):
        return True
    # A deliberately preserved open/damaged region is not a successful closed
    # repair. Accept it only as an explicitly reported partial result whose
    # actual topology has not worsened; the engines independently verify shape.
    before = (report.get("before") or {}).get("stats") or {}
    return (report.get("method") == "surface_preserving"
            and report.get("needs_review") is True
            and stats.get("components") == before.get("components")
            and stats.get("duplicate_faces") == stats.get("degenerate_faces") == 0
            and all(isinstance(stats.get(key), int) and isinstance(before.get(key), int)
                    and stats[key] <= before[key] for key in _STRUCTURAL_PROBLEMS))


def reset_outdated_processing(model: dict) -> None:
    """Retire v1 output and restart from the retained source, never its voxels."""
    current = model.get("processing") or {}
    final = model.get("processed") or {}
    outdated = (current.get("version") and current["version"] != PROCESSOR_VERSION) or (
        final.get("version") and final["version"] != PROCESSOR_VERSION)
    if not outdated:
        return
    if final:
        model["previous_processed"] = model.pop("processed")
    model["processing"] = {"version": PROCESSOR_VERSION, "source_sha256": model.get("sha256"),
                           "stage": "repair", "status": "pending", "attempts": [],
                           "message": "将从原始 STL 重新进行保形处理。"}


def process_stage(raw: bytes, stage: str) -> tuple[dict, bytes | None, str | None]:
    """Return report, verified intermediate/final bytes, and the next stage.

    Each attempt gets its own request budget. A fallback always receives the
    original/repaired source, never a partially failed output.
    """
    if stage == "repair":
        from .mesh_repair import process_repair
        report, output = process_repair(raw)
        next_stage = "smooth" if output is not None else "preserve"
    elif stage == "preserve":
        from .mesh_repair import process_preserving_repair
        report, output = process_preserving_repair(raw)
        next_stage = "smooth" if output is not None else None
    elif stage in _SMOOTHING:
        strength, fallback = _SMOOTHING[stage]
        report, output = process_smoothing(raw, strength=strength, subdivide=False, preserve_defects=True)
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
                or report.get("status") not in {"repaired", "partial", "smoothed", "unchanged"}
                or not _verified(report, output, raw)):
            # Never deliver this output; try the next method using the last
            # verified source. Source digest mismatches above remain fatal.
            report.update(status="rejected", code="STL_OUTPUT_UNVERIFIED",
                          message="输出未通过复检，尝试其他处理方式。")
            next_stage = "preserve" if stage == "repair" else _SMOOTHING.get(stage, (None, None))[1]
            return report, None, next_stage
    return report, output, next_stage
