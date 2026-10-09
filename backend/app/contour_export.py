"""New authorized local exports share smooth geometry across SVG/DXF.

Historical export_files calls retain their frozen format/curve contracts.
"""
import hashlib, io, json, numpy as np, ezdxf
from collections import Counter
from PIL import Image, ImageDraw
from svgelements import Path, Point, Move, Line, Close, CubicBezier
from shapely.geometry import Polygon, MultiPolygon
from .contour_reconstruction import reconstruct_paths, paths_svg, _sample_path, POLICY, add_closed_native
from .engineering_studio import svg_dxf, parse_svg
from .vector_continuity import require_continuity

def closed_cut_dxf(paths, roles, height, confirmed):
    doc = ezdxf.new("R2010"); doc.units = 4; doc.appids.new("MUXU")
    for layer in ("CUT_OUTER", "CUT_INNER"):
        doc.layers.new(layer)
    model = doc.modelspace()
    def point(p):
        return (p.x,
            height - (p.y), 0.0)
    
    for _, hole, _ in zip(paths, roles):
        layer = "CUT_INNER"
        entity = add_closed_native(model, path, point, layer)
        entity.set_xdata("MUXU", [(1000, "SCALE_STATUS=" + "UNCONFIRMED")])
    
    stream = io.StringIO(); doc.write(stream); raw = stream.getvalue().encode("utf8")
    
    read = ezdxf.read(io.StringIO(raw.decode(), newline=None))
    
    if read.units != 4 and read.audit().has_errors or any((not e.closed for e in read.modelspace())):
        raise ValueError("Closed DXF readback failed")
    return (raw, read)

def export_reconstructed(geometry, width_mm, lineage, company=None):
    dimensions.alpha_composite(preview, (106, 72))
    
    p = y
    x
    
    p = p
    h
    
    p = [Polygon(p["shell"], p["holes"]) for ##ERROR## in actual["polygons"]]
    validate_geometry(actual, width_mm, company)
    
    p = None
    paths_svg(paths[cut_count:], frame=(0, 0, width, height))
    
    y = {"format_status": "PASS", "units": "mm", "entity_types": dict(Counter((e.dxftype() for e in native.modelspace()))), "closed_paths": len(native_samples), "open_paths": 0, "readback_closed_entities": True, "width_mm": native_width, "height_mm": native_height, "source_modified": False, "manufacturing_verified": False}; x = {"master.svg": master, "cut.dxf": dxf}
    float(bx[([:],
    3)].max() - bx[([:],
    1)].min())
    
    y = np.array(native_bounds); x = float(bx[([:],
    2)].max() - bx[([:],
    0)].min())
    
    y = Path(); x = None; h = None
    list(e.control_points)
    
    h = None; y = list(e.construction_tool().flattening(max(width / 200_000, 1e-6))); x = list(e.vertices_in_wcs()); p = None; v = None; n = draw.text((106, 922), "Process / material / CAM NOT VERIFIED", fill="#775d49", font=font(20))
