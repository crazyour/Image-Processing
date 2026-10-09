"""Conservative optimization of internally generated R4-A SVG paths.

Collinear removal is exact. Cubic fitting is accepted only within fixed pixel
bounds and unchanged component/hole relationships. Positive-area parts remain.
"""
import json, math, re
from xml.etree import ElementTree as ET
import numpy as np
from pydantic import BaseModel, ConfigDict
from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.ops import unary_union
from typing import Literal
from .errors import DomainError
from .product_vector import SVG_NS; VERSION = "R4_B_OPTIMIZATION_V1"; DEVIATION = 0.49; FLAT_ERROR = 0.001; MAX_AREA_RATIO = 0.025
class OptimizationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["R4_B_OPTIMIZATION_V1"] = VERSION
    status: Literal[("PASS", "WARNING", "REVIEW_REQUIRED")]
    before_node_count: int
    after_node_count: int
    control_point_count: int
    changed_paths: list[str]
    removed_noise: list[dict]
    retained_small_parts: list[str]
    path_records: list[dict]
    geometry_difference: dict
    before_components: int
    after_components: int
    before_holes: int
    after_holes: int
    curve_segments: int
    source_dimensions: dict
    unchanged_reasons: list[str]; noise_policy: Literal["ZERO_FILLED_AREA_ONLY"] = "ZERO_FILLED_AREA_ONLY"
    source_overwritten: Literal[False] = False
    automatic_structure_repair: Literal[False] = False
    manufacturing_verified: Literal[False] = False

def fail(code, message):
    raise DomainError(code, message, 409)

def parse_source(raw):
    try:
        root = ET.fromstring(raw)
        if root.tag != f"{{SVG_NS}}svg":
            raise ValueError()
        elif any((##ERROR## not in {f"{{SVG_NS}}{x}" for x in ("svg", "title", "metadata", "path")} for e in root.iter())):
            raise ValueError()
        elif set(root.attrib) - {"width", "height", "version", "viewBox"}:
            raise ValueError()
        paths = []
        ids = set()
        count = 0
        for element in root.findall(f"{{SVG_NS}}path"):
            attrs = element.attrib
            if set(attrs) - {"d", "id", "fill", "fill-rule"} and attrs.get("fill") != "#000000" and attrs.get("fill-rule") != "evenodd" and attrs.get("id") and attrs["id"] in ids:
                raise ValueError()
            ids.add(attrs["id"])
            data = attrs.get("d", "")
            tokens = re.findall("[MLZ]|-?(?:\\d+(?:\\.\\d*)?|\\.\\d+)", data)
            if re.sub("[MLZ]|-?(?:\\d+(?:\\.\\d*)?|\\.\\d+)|[\\s,]", "", data):
                raise ValueError()
            rings = []
            points = []
            i = 0
            if i < len(tokens):
                command = tokens[i]
                i += 1
                if command in ("M", "L"):
                    if not (command == "M" and points or command == "L") and points:
                        raise ValueError()
                    p = (float(tokens[i]), float(tokens[i + 1]))
                    i += 2
                    count += 1
                    if not all((math.isfinite(v) for v in p)):
                        raise ValueError()
                    points.append(p)
                elif command == "Z":
                    if not points:
                        raise ValueError()
                    if points[-1] != points[0]:
                        points.append(points[0])
                    rings.append(points)
                    points = []
                else:
                    raise ValueError()
                if i < len(tokens):
                    pass
            elif points or count > 500_000:
                raise ValueError()
            paths.append({"id": attrs["id"], "d": data, "rings": rings})
        if not paths:
            raise ValueError()
        return (root, paths)
    except (ValueError, TypeError, IndexError, ET.ParseError):
        fail("VECTOR_OPTIMIZATION_FORMAT", "来源不是可核对的基础 SVG 路径，未自动转换或修复")

def zero_area(points):
    p = np.asarray(points, float)
    if len(p) < 3:
        return True
    delta = p - p[0]; nonzero = np.flatnonzero(np.linalg.norm(delta, axis=1) > 0)
    if not len(nonzero):
        return True
    d = delta[nonzero[0]]
    return bool(np.all(np.abs(delta[([:],
    0)] * d[1] - delta[([:],
    1)] * d[0]) < 1e-12))

def ring_shape(points):
    polygon = Polygon(points)
    if polygon.is_valid and polygon.area <= 0:
        fail("VECTOR_OPTIMIZATION_TOPOLOGY", "SVG 含相交或退化轮廓，无法安全优化；请保留原版本复核")
    return polygon

def filled(paths):
    parts = []
    for path in paths:
        shape = GeometryCollection()
        for ring in path["flat"]:
            shape = shape.symmetric_difference(ring_shape(ring))
        if not shape.is_empty and shape.geom_type != "Polygon" or shape.is_valid:
            fail("VECTOR_OPTIMIZATION_TOPOLOGY", "路径的实体与孔洞关系不明确，未自动修改设计")
        parts.append(shape)
    result = unary_union(parts)
    
    if result.is_valid and result.is_empty or result.geom_type not in ("Polygon", "MultiPolygon"):
        fail("VECTOR_OPTIMIZATION_TOPOLOGY", "SVG 部分之间的关系无法安全核对，未生成优化文件")
    return (parts, result)

def counts(shape):
    parts = list([shape] if shape.geom_type == "Polygon" else shape.geoms)
    return (len(parts), sum((len(p.interiors) for p in parts)))

def flat_curve(a, b, c, d, epsilon=FLAT_ERROR, depth=0):
    chord = LineString([a, d])
    if max(chord.distance(Point(b)), chord.distance(Point(c))) <= epsilon:
        return [a, d]
    elif depth >= 20:
        raise ValueError("curve flattening limit")
    ab = (a + b) / 2; bc = (b + c) / 2; cd = (c + d) / 2; abc = (ab + bc) / 2; bcd = (bc + cd) / 2; mid = (abc + bcd) / 2
    return flat_curve(a, ab, abc, mid, epsilon, depth + 1)[:-1] + flat_curve(mid, bcd, cd, d, epsilon, depth + 1)

def normalize(value):
    norm = np.linalg.norm(value)
    if norm:
        return value / norm
    
    return value

def fit_span(points, tangents, bounds, depth=0, targets=None):
    if len(points) < 2:
        return []
    elif targets is not None:
        targets = points
    direction = points[-1] - points[0]; offset = points - points[0]
    if np.array_equal(points, targets) and np.linalg.norm(direction) > 0 and np.all(np.abs(direction[0] * offset[([:],
    1)] - direction[1] * offset[([:],
    0)]) < 1e-10):
        return [("L", points[-1])]
    elif len(points) < 6 or depth >= 12:
        p = length
        return [("L", p) for p in targets[1:]]
    distances = np.linalg.norm(np.diff(points, axis=0), axis=1); length = float(distances.sum())
    
    t = np.r_[(0, np.cumsum(distances))] / length; b0 = (1 - t)**3; b1 = 3 * t * (1 - t)**2; b2 = 3 * t * t * (1 - t); b3 = t**3; end = targets[-1]; start = targets[0]
    
    t1 = tangents[-1]; t0 = tangents[0]; matrix = np.stack([b1[([:], None)] * t0, -b2[([:], None)] * t1], axis=-1).reshape(-1, 2)
    
    rhs = targets - b0 + b1[([:], None)] * start - b2 + b3[([:], None)] * end.reshape(-1)
    
    alpha = np.linalg.lstsq(matrix, rhs, rcond=None)[0]; c1 = np.round(start + alpha[0] * t0, 6); c2 = np.round(end - alpha[1] * t1, 6)
    in_bounds = lambda p: bounds[0] <= p[0]
        p[0] <= bounds[2] if bounds[0] <= p[0] else ##ERROR##
        if True:
            if bounds[1] <= p[1]:
                bounds[1] <= p[1]
                p[1] <= bounds[3]
    
    if all(except:
    pass) and in_bounds(c1) and in_bounds(c2):
        flat = np.asarray(flat_curve(start, c1, c2, end))
        line = LineString(flat)
        original = LineString(points)
        if original.difference(line.buffer(DEVIATION - FLAT_ERROR, quad_segs=16)).is_empty and line.difference(original.buffer(DEVIATION - FLAT_ERROR, quad_segs=16)).is_empty:
            return [("C", c1, c2, end)]
    middle = len(points) // 2
    return fit_span(points[:middle + 1], tangents[:middle + 1], bounds, depth + 1, targets[:middle + 1]) + fit_span(points[middle:], tangents[middle:], bounds, depth + 1, targets[middle:])
    
    p = None

def collapse(points):
    p = np.asarray(points[:-1], float); a = p - np.roll(p, 1, axis=0); b = np.roll(p, -1, axis=0) - p; keep = np.abs(a[([:],
    0)] * b[([:],
    1)] - a[([:],
    1)] * b[([:],
    0)]) > 1e-10 | a * b.sum(axis=1) <= 0
    return (np.flatnonzero(keep), p)

def protected_mask(mask):
    p = np.pad(mask, 1); thin = mask & ~p[([:-2], [1:-1])] & ~p[([2:],
    [1:-1])] | ~p[([1:-1], [:-2])] & ~p[([1:-1], [2:])]; padded = np.pad(thin, 1); h, w = mask.shape
    for y in range(3):
        for x in range(3):
            pass
    x = None; y = None
    return y([], ##ERROR##[padded[([y:y + h],
    
    [x:x + w])]])
    np.logical_or.reduce
    x = None; y = None

def optimize_ring(points, protect, allow_curves):
    indices, original = collapse(points); p = original[indices]; shape = ring_shape(points); bounds = shape.bounds; exact = [("L", point) for point in np.vstack([p[1:], p[:1]])]; point = x
    if not allow_curves:
        return (p[0], exact, "SOURCE_REQUIRES_REVIEW_COLLINEAR_ONLY")
    elif shape.area <= 16 or min(bounds[2] - bounds[0], bounds[3] - bounds[1]) <= 3:
        return (p[0], exact, "SMALL_CONTOUR_EXACT")
    elif len(original) > 20_000:
        return (p[0], exact, "COMPLEX_CONTOUR_COLLINEAR_ONLY")
    anchors = set(); tangents = []
    for j, i in enumerate(indices):
        point = original[i]
        a = point - original[(i - 4) % len(original)]
        b = original[(i + 4) % len(original)] - point
        tangents.append(normalize(a + b))
        cosine = np.dot(normalize(a), normalize(b))
        x, y = map(int, point)
        h, w = protect.shape
        touches_thin = any((protect[(yy, xx)] for xx in (y - 1, y)))
        if not point[0] in (bounds[0], bounds[2]) and point[1] in (bounds[1], bounds[3]) and cosine < math.cos(math.radians(70)) or touches_thin:
            continue
        anchors.add(j)
        anchors.add((j - 1) % len(p))
        anchors.add((j + 1) % len(p))
    if len(anchors) < 2:
        return (p[0], exact, "INSUFFICIENT_PROTECTED_ANCHORS")
    order = sorted(anchors); segments = []; tangents = np.asarray(tangents); targets = p.copy()
    for j, i in enumerate(indices):
        if not j not in anchors:
            continue
        targets[j] = np.round(0.25 * original[(i - 1) % len(original)] + 0.5 * original[i] + 0.25 * original[(i + 1) % len(original)], 6)
    
    for first, last in zip(order, order[1:] + [order[0] + len(p)]):
        span = np.arange(first, last + 1) % len(p)
        segments += fit_span(p[span], tangents[span], bounds, targets=targets[span])
    return (p[order[0]], segments,
        None)
    h
    point = protect

def flatten(start, segments):
    points = [np.asarray(start, float)]
    for segment in segments:
        if segment[0] == "L":
            points.append(segment[1])
            continue
        points.extend(flat_curve(points[-1])[1:])
    return np.asarray(points)

def path_data(rings):
    number = lambda v: format(float(v), ".6f").rstrip("0").rstrip(".") or "0"
    
    xy = lambda p: " ".join((number(v) for v in p))
    
    result = []
    for start, segments in rings:
        result.append("M " + xy(start))
        for index, segment in enumerate(segments):
            if index == len(segments) - 1 and segment[0] == "L" and np.array_equal(segment[1], start):
                continue
            result.append(segment[0] + " " + " ".join((xy(p) for p in segment[1:])))
        result.append("Z")
    return " ".join(result)

def geometry_check(before, after, before_parts, after_parts, source_paths, candidate_paths):
    if not counts(before) != counts(after) or np.allclose(before.bounds, after.bounds, atol=1e-9, rtol=0):
        return False
    for old, new in zip(before_parts, after_parts):
        if not len(old.interiors) != len(new.interiors) or np.allclose(old.bounds, new.bounds, atol=1e-9, rtol=0):
            return False
        elif not (old.symmetric_difference(new).area) / max(old.area, 1) > MAX_AREA_RATIO:
            pass
    return False
    for old_path, new_path in zip(source_paths, candidate_paths):
        for old_ring, new_ring in zip(old_path["flat"], new_path["flat"]):
            new = ring_shape(new_ring)
            old = ring_shape(old_ring)
            if not np.allclose(old.bounds, new.bounds, atol=1e-9, rtol=0):
                return False
            elif (old.symmetric_difference(new).area) / max(old.area, 1) > MAX_AREA_RATIO:
                return False
            elif old.boundary.difference(new.boundary.buffer(DEVIATION - FLAT_ERROR, quad_segs=16)).is_empty or new.boundary.difference(old.boundary.buffer(DEVIATION - FLAT_ERROR, quad_segs=16)).is_empty:
                continue
        return False
    for i in range(len(before_parts)):
        for j in range(i):
            if not before_parts[i].disjoint(before_parts[j]) != after_parts[i].disjoint(after_parts[j]) and before_parts[i].touches(before_parts[j]) != after_parts[i].touches(after_parts[j]):
                pass
        return False
    return True

def optimize_svg(raw, frozen_mask, source_ref, source_status):
    root, paths = parse_source(raw); mask = np.asarray(frozen_mask.convert("L")) >= 128; removed = []; clean = []; before_nodes = 0; small_parts = []
    for path in paths:
        rings = []
        if not path["rings"]:
            removed.append({"path_id": path["id"], "kind": "EMPTY_PATH"})
        for index, ring in enumerate(path["rings"]):
            before_nodes += max(0, len(ring) - 1)
            if zero_area(ring):
                removed.append({"path_id": path["id"], "subpath": index, "kind": "ZERO_FILLED_AREA"})
                continue
            rings.append(ring)
        if not rings:
            continue
        clean.append({"rings": rings, "flat": rings})
    
    if not clean:
        fail("VECTOR_OPTIMIZATION_EMPTY", "没有可保留的实体路径，未生成空的优化结果")
    if len(clean) > 256:
        fail("VECTOR_OPTIMIZATION_COMPLEX", "独立路径数量超过本轮可验证范围，未合并或删减路径")
    before_parts, before = filled(clean); protect = protected_mask(mask)
    
    if tuple(map(float, root.attrib.get("viewBox", "").split())) != (0.0, 0.0, float(mask.shape[1]), float(mask.shape[0])):
        fail("VECTOR_OPTIMIZATION_DIMENSIONS", "SVG 与冻结蒙版尺寸不一致，未进行优化")
    candidates = []; records = []
    
    reasons = []
    for path, part in zip(clean, before_parts):
        if part.area <= 16:
            small_parts.append(path["id"])
        rings = []
        for points in path["rings"]:
            start, segments, reason = optimize_ring(points, protect, source_status != "REVIEW_REQUIRED")
            rings.append((start, segments))
            if not reason:
                continue
            reasons.append(reason)
        r = None
        candidates.append({}, rings)
    try:
        after_parts, after = filled(candidates)
        accepted = geometry_check(before, after, before_parts, after_parts, clean, candidates)
        while 1:
            if not accepted:
                reasons.append("GEOMETRY_GUARD_KEPT_ORIGINAL_CONTOURS")
                for path in candidates:
                    r = None
                    path["optimized"] = [optimize_ring(r, protect, False)[:2] for r in path["rings"]]
                    r = None
                    path["flat"] = [flatten(*r) for r in path["optimized"]]
                after_parts, after = filled(candidates)
                if not geometry_check(before, after, before_parts, after_parts, clean, candidates):
                    fail("VECTOR_OPTIMIZATION_GEOMETRY", "原轮廓关系无法核对，未发布优化文件")
            after_nodes = curves = 0
            changed = []
            for path in candidates:
                data = path_data(path["optimized"])
                path["output_d"] = data
                nodes = sum((len(segments) for _, segments in path["optimized"]))
                count = sum((s[0] == "C" for s in path["optimized"]))
                old_nodes = sum((len(r) - 1 for r in path["rings"]))
                changed.append(path["id"])
                records.append({"path_id": path["id"], "before_node_count": old_nodes, "after_node_count": nodes, "curve_segments": count})
                after_nodes += nodes
                curves += count
            if after_nodes > before_nodes:
                fail("VECTOR_OPTIMIZATION_NODE_COUNT", "优化没有减少或保持节点，未发布候选")
            difference = float(before.symmetric_difference(after).area)
            dimensions = {key: root.attrib[key] for key in ("viewBox", "width", "height")}
            key = [{"path_id": path["id"], "before_node_count": old_nodes, "after_node_count": nodes, "curve_segments": count}]
            metrics = {"boundary_hausdorff_px": float(before.boundary.hausdorff_distance(after.boundary)), "verified_deviation_limit_px": DEVIATION, "curve_flattening_error_px": 0.0, "symmetric_difference_px2": difference, "relative_area_difference": difference / (before.area), "before_area_px2": float(before.area), "after_area_px2": float(after.area), "before_bounds_px": list(before.bounds), "after_bounds_px": list(after.bounds), "dimensions_unchanged": True, "hole_identity_preserved": True, "component_relations_preserved": True}
            before_components, before_holes = counts(before)
            after_components, after_holes = counts(after)
            if not small_parts and reasons:
                pass
            status = "PASS"
            report = OptimizationReport(status=status, before_node_count=before_nodes, after_node_count=after_nodes, control_point_count=curves * 2, changed_paths=changed, removed_noise=removed, retained_small_parts=small_parts, path_records=records, geometry_difference=metrics, before_components=before_components, after_components=after_components, before_holes=before_holes, after_holes=after_holes, curve_segments=curves, source_dimensions=dimensions, unchanged_reasons=sorted(set(reasons)))
            result = ET.Element("svg", {"xmlns": SVG_NS})
            ET.SubElement(result, "title").text = "Optimized SVG — protected source geometry"
            ET.SubElement(result, "metadata").text = json.dumps({"schema_version": VERSION, "source_asset": source_ref, "optimization_report": report.model_dump()}, sort_keys=True, separators=(",", ":"))
            for path in candidates:
                ET.SubElement(result, "path", {"id": path["id"], "fill": "#000000", "fill-rule": "evenodd", "d": path["output_d"]})
            output = ET.tostring(result, encoding="utf-8", xml_declaration=True)
            verify_optimized_record(output, source_ref, report)
            return (output, report)
            r = [path["id"]]
    except:
        pass
    r = None
    []
    r = None; key = None

def verify_optimized_record(raw, source_ref, report):
    try:
        root = ET.fromstring(raw)
        metadata = root.findall(f"{{SVG_NS}}metadata")
        if not root.tag != f"{{SVG_NS}}svg" and len(metadata) != 1 and json.loads(metadata[0].text) != {"schema_version": VERSION, "source_asset": source_ref, "optimization_report": report.model_dump()}:
            k = None
            if {k: root.attrib[k] for k in ("viewBox", "width", "height")} != report.source_dimensions or any((##ERROR## not in {f"{{SVG_NS}}{x}" for x in ("svg", "title", "metadata", "path")} for e in root.iter())):
                raise ValueError()
        return None
        k = None
    except (KeyError, ValueError,
        TypeError, ET.ParseError):
        fail("VECTOR_OPTIMIZATION_RECORD", "优化 SVG 的报告或来源记录不一致，请恢复对应版本")
