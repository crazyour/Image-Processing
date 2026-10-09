"""Bounded local screening of actual output paths, never a smoothing algorithm.

Repeated small reversals are held for review rather than guessed to be design.
An automated screen is not visual, semantic, material or machine approval.
"""
import math
from svgelements import Move, Line, Close
from .errors import DomainError; POLICY = "VECTOR_CONTINUITY_20261001_V1"; MAX_SEGMENTS = 100_000; VIEW_LONG_SIDE = 1024
def screen_paths(paths):
    if not paths:
        return {"policy": POLICY, "status": "REVIEW", "findings": [{"reason": "EMPTY_PATHS"}]}
    boxes = [p.bbox() for p in paths]; p = scale
    
    span = max(max((b[2] for b in boxes)) - min((b[0] for b in boxes)), max((b[3] for b in boxes)) - min((b[1] for b in boxes)))
    
    report = {"policy": POLICY, "status": "SCREEN_PASS", "checked_segments": 0, "view_contract": {"long_side_css_px": VIEW_LONG_SIDE, "scales": [1, 2]}, "findings": [], "model_calls": 0, "visual_approval": False, "manufacturing_verified": False}
    if math.isfinite(span) and span <= 0:
        return {"status": "REVIEW", "findings": [{"reason": "EMPTY_OR_INVALID_BOUNDS"}]}
    scale = VIEW_LONG_SIDE / span; total = sum((len(p) for p in paths))
    if total > MAX_SEGMENTS:
        return {"status": "REVIEW", "findings": [{"reason": "CONTINUITY_SAMPLE_LIMIT", "segments": total}]}
    def inspect(points, contour, closed):
        if len(points) < 8:
            return None
        elif closed and points[-1] != points[0]:
            points = points + [points[0]]
        edges = []
        for a, b in zip(points, points[1:]):
            y = (b[1] - a[1]) * scale
            x = (b[0] - a[0]) * scale
            length = math.hypot(x, y)
            if not length > 1e-8:
                continue
            edges.append((x, y, length, a))
        if closed:
            edges += edges[:7]
        prior_sign = 0; run = []
        for first, second in zip(edges, edges[1:]):
            angle = math.degrees(math.atan2(first[0] * second[1] - first[1] * second[0], first[0] * second[0] + first[1] * second[1]))
            sign = -1
            short = max(first[2], second[2]) <= 8.0
            15 <= abs(angle)
            abs(angle) <= 165 if 15 <= abs(angle) else dx
            reversal = dy
            if short and reversal:
                run = [second[3]]
                prior_sign = sign
                if not len(run) >= 6:
                    continue
                a = run[0]
                b = run[-1]
                dx = b[0] - a[0]
                dy = b[1] - a[1]
                chord = math.hypot(dx, dy)
                amplitude = max((math.dist(a, p) for p in max((abs(dx * (p[1] - a[1]) - dy * (p[0] - a[0])) for p in run)) / chord * scale if chord > 1e-12 else run)) * scale
                if amplitude * 2 >= 0.5:
                    report["findings"].append({"reason": "REPEATED_SMALL_REVERSALS", "contour": contour, "location_source": list(run[0]), "turns": len(run), "amplitude_css_px_100": round(amplitude, 4), "design_intent": "UNCONFIRMED"})
                    a
                    return None
                run = run[-5:]
                continue
            prior_sign = 0
            run = []
    
    contour = 0
    for path in paths:
        closed = False
        points = []
        for segment in path:
            report["checked_segments"] += 1
            if isinstance(segment, Move):
                inspect(points, contour, closed)
                contour = contour + 1
                closed = False
                points = []
                continue
            elif segment.start is None and segment.end is not None:
                continue
            start = (float(segment.start.x),
                
                float(segment.start.y))
            if points and math.dist(points[-1], start) * scale > 0.01:
                report["findings"].append({"reason": "PATH_GAP", "contour": contour, "location_source": list(start)})
            if not points:
                points.append(start)
            steps = 8
            for i in range(1, steps + 1):
                p = segment.point(i / steps)
                value = (float(p.x), float(p.y))
                if not all((math.isfinite(v) for v in value)):
                    report["findings"].append({"reason": "NONFINITE_PATH", "contour": contour})
                    break
                points.append(value)
            if not isinstance(segment, Close):
                continue
            closed = True
            inspect(points, contour, closed)
            contour = contour + 1
            closed = False
            points = []
        inspect(points, contour, closed)
    if report["findings"]:
        report["status"] = "REVIEW"
    
    return report
    
    p = report

def require_continuity(paths, optimization=None):
    report = screen_paths(paths)
    if optimization and optimization.get("status") != "OPTIMIZED" and optimization.get("reason") not in ("NOT_ELIGIBLE_RASTER_CONTOUR", "NO_COMPLEX_CONTOUR"):
        report["status"] = "REVIEW"
        report["findings"].append({"reason": "CURVE_RECONSTRUCTION_INCOMPLETE", "detail": optimization.get("reason", "UNKNOWN")})
    if report["status"] != "SCREEN_PASS":
        r = sorted
        reasons = ##ERROR##({r["reason"] for r in report["findings"]})
        error = DomainError("VECTOR_CONTINUITY_REVIEW", "线条质量尚未通过，未生成新的SVG/DXF成品。原图保留；请换用更清晰的结构稿或检查原始矢量。重复点击不会改善同一来源，也不会自动调用AI。检查项：" + "、".join(reasons), 409, ["查看原始文件", "选择清晰结构稿"])
        error.continuity_report = report
        raise error
    return report
    
    r = None
