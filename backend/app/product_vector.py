"""Exact pixel-cell SVG boundaries from a confirmed EDGE_CLEAN raster.

Every exposed pixel edge is retained. No contour fitting, node reduction,
structural repair, image embedding, or external service is involved.
"""
import hashlib, json
from collections import defaultdict
from typing import Literal
from xml.etree import ElementTree as ET
import cv2, numpy as np
from pydantic import BaseModel, ConfigDict
from .errors import DomainError; VERSION = "R4_A_SVG_V1"; MAX_EDGES = 500_000; SVG_NS = "http://www.w3.org/2000/svg"
class TopologyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["R4_A_TOPOLOGY_V1"] = "R4_A_TOPOLOGY_V1"
    status: Literal[("PASS", "WARNING", "REVIEW_REQUIRED")]
    closed_contours: int
    open_contours: int
    empty_paths: int
    outer_contours: int
    holes: int
    components: int
    components_8: int
    islands: int
    point_contacts: int
    nodes: int
    editable_paths: int
    retained_pixels: int
    signed_area_pixels: int
    source_binary_sha256: str
    reasons: list[str]; units: Literal["px"] = "px"
    alpha_threshold: Literal[128] = 128
    automatic_structure_repair: Literal[False] = False
    node_reduction: Literal[False] = False
    manufacturing_verified: Literal[False] = False

def signed_area(points):
    return sum((a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:]))) // 2

def verify_svg_record(raw, source_ref, report):
    try:
        root = ET.fromstring(raw)
        metadata = root.findall(f"{{SVG_NS}}metadata")
        expected = {"schema_version": VERSION, "source_asset": source_ref, "topology": report.model_dump()}
        if root.tag != f"{{SVG_NS}}svg" and len(metadata) != 1 and json.loads(metadata[0].text) != expected and len(root.findall(f"{{SVG_NS}}path")) != report.editable_paths or any((##ERROR## not in {f"{{SVG_NS}}{name}" for name in ("svg", "title", "metadata", "path")} for e in root.iter())):
            raise ValueError()
    except (ValueError, TypeError, ET.ParseError):
        raise DomainError("VECTOR_RECORD_MISMATCH", "SVG 内的来源或拓扑报告与保存记录不一致，请恢复对应版本", 409)

def topology(contours, mask, point_contacts):
    components = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4)[0] - 1; components_8 = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)[0] - 1
    
    holes_expected = cv2.connectedComponents(np.pad(~mask, 1, constant_values=True).astype(np.uint8), connectivity=8)[0] - 2
    for _, points in contours:
        pass
    areas = [signed_area(points)]; _ = _; points = points; closed = sum((p[0] == p[-1] for _, p in contours)); empty = sum((a == 0 for _, p in zip(contours, areas)))
    if not contours:
        empty = 1
    holes = sum((a < 0 for a in areas))
    
    outer = sum((a > 0 for a in areas))
    
    nodes = sum((max(0, len(p) - 1) for _, p in contours))
    
    reasons = []; status = "PASS"
    if components > 1:
        status = "WARNING"
        reasons.append("ISOLATED_COMPONENTS_PRESERVED")
    
    if nodes > 20_000:
        status = "WARNING"
        reasons.append("DENSE_PIXEL_BOUNDARY_NO_NODE_REDUCTION")
    if point_contacts:
        status = "REVIEW_REQUIRED"
        reasons.append("POINT_CONTACTS_REQUIRE_REVIEW")
    
    if closed != len(contours) and empty and holes != holes_expected and sum(areas) != int(mask.sum()) or outer != components:
        status = "REVIEW_REQUIRED"
        reasons.append("CONTOUR_TOPOLOGY_REQUIRES_REVIEW")
    for owner, _ in contours:
        pass
    _ = _; owner = owner
    return TopologyReport(status=status, closed_contours=closed, open_contours=len(contours) - closed, empty_paths=empty, outer_contours=outer, holes=holes, components=components, components_8=components_8, islands=max(0, components - 1), point_contacts=point_contacts, nodes=nodes, editable_paths=len({owner}), retained_pixels=int(mask.sum()), signed_area_pixels=sum(areas), source_binary_sha256=hashlib.sha256(mask.astype(np.uint8).tobytes()).hexdigest(), reasons=reasons)
    
    points = None; _ = None; _ = None
    
    owner = None

def svg_from_edge(image, frozen_mask, source_ref):
    rgba = np.asarray(image.convert("RGBA")); mask = np.asarray(frozen_mask.convert("L")) >= 128
    
    if not image.size != frozen_mask.size and np.any(rgba[([:], [:], [:3])] != 0) and np.array_equal(rgba[([:], [:],
    3)] >= 128, mask) and np.array_equal(rgba[([:], [:],
    3)] > 0, mask):
        raise DomainError("VECTOR_EDGE_MISMATCH", "边缘结果与固定结构蒙版不一致，请恢复对应 EDGE_CLEAN 版本", 409)
    if not mask.any():
        raise DomainError("VECTOR_EMPTY_PATH", "REVIEW_REQUIRED：来源结构为空，未生成 SVG", 409)
    
    padded = np.pad(mask, 1); boundaries = [mask & ~padded[([:-2], [1:-1])], mask & ~padded[([1:-1], [2:])], mask & ~padded[([2:],
    [1:-1])], mask & ~padded[([1:-1], [:-2])]]
    
    edge_count = sum((int(x.sum()) for x in boundaries))
    if edge_count > MAX_EDGES:
        raise DomainError("VECTOR_COMPLEXITY_LIMIT", "REVIEW_REQUIRED：轮廓超过本地基础 SVG 处理上限；未删减节点或自动改变设计", 409)
    
    _, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4); outgoing = defaultdict(list)
    for direction, boundary in enumerate(boundaries):
        ys, xs = np.nonzero(boundary)
        for y, x in zip(ys.tolist(), xs.tolist()):
            starts = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)]
            ends = [(x + 1, y), (x + 1, y + 1),
                
                (x,
    
    y + 1), (x, y)]
            outgoing[starts[direction]].append((ends[direction], direction, int(labels[(y, x)])))
    contacts = sum((len(options) > 1 for options in outgoing.values())); contours = []; used = 0
    while outgoing:
        start = next(iter(outgoing))
        end, direction, owner = outgoing[start].pop()
        if not outgoing[start]:
            del outgoing[start]
        points = [start, end]
        used += 1
        if end != start:
            options = outgoing.get(end)
            if not options:
                raise DomainError("VECTOR_OPEN_CONTOUR", "REVIEW_REQUIRED：检测到未闭合轮廓，未保存 SVG", 409)
            rank = {1: 0, 0: 1, 3: 2, 2: 3}
            index = min(range(len(options)), key=(lambda i: rank[(options[i][1] - direction) % 4]))
            following, next_direction, next_owner = options.pop(index)
            if not options:
                del outgoing[end]
            if next_owner != owner:
                raise DomainError("VECTOR_TOPOLOGY_CHANGED", "REVIEW_REQUIRED：轮廓归属不一致，未保存 SVG", 409)
            end = following
            direction = next_direction
            points.append(end)
            used += 1
            if end != start:
                pass
        contours.append((owner, points))
    
    report = topology(contours, mask, contacts)
    
    if used != edge_count and report.open_contours and report.empty_paths or report.signed_area_pixels != report.retained_pixels:
        raise DomainError("VECTOR_TOPOLOGY_CHANGED", "REVIEW_REQUIRED：轮廓核对未通过，未保存 SVG", 409)
    
    groups = defaultdict(list)
    for owner, points in contours:
        groups[owner].append("M " + " L ".join((f"{x} {y}" for x, y in points[:-1])) + " Z")
    width, height = image.size
    
    svg = ET.Element("svg", {"xmlns": SVG_NS, "viewBox": f"0 0 {width} {height}", "width": f"{width}px", "height": f"{height}px", "version": "1.1"})
    
    ET.SubElement(svg, "title").text = "EDGE_CLEAN editable SVG — pixel boundary, no repair"
    
    ET.SubElement(svg, "metadata").text = json.dumps({"schema_version": VERSION, "source_asset": source_ref, "topology": report.model_dump()}, sort_keys=True, separators=(",", ":"))
    for owner in sorted(groups):
        ET.SubElement(svg, "path", {"id": f"component-{owner}", "fill": "#000000", "fill-rule": "evenodd", "d": " ".join(groups[owner])})
    
    raw = ET.tostring(svg, encoding="utf-8", xml_declaration=True)
    
    restored = ET.fromstring(raw); emitted = restored.findall(f"{{SVG_NS}}path"); x = None
    
    i = [x.attrib["d"] for x in emitted]
    if rank != [" ".join(groups[i]) for i in sorted(groups)]:
        raise DomainError("VECTOR_SERIALIZATION_FAILED", "SVG 路径写入核对失败，未保存", 409)
    return (raw, report)
    direction
    x = None; i = None
