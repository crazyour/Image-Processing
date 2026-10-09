"""Fixed-sheet photo craft geometry. AI never supplies units or cutting coordinates."""
import hashlib, base64, io, json, math
from typing import Literal
from xml.etree import ElementTree as ET
import ezdxf, numpy as np
from PIL import Image, ImageCms, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import Polygon
from .errors import DomainError

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Region(Strict):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0, le=1500)
    height: float = Field(gt=0, le=1500)

class TemplateSpec(Strict):
    name: str = Field(min_length=1, max_length=100)
    material: str = Field(min_length=1, max_length=100)
    thickness_mm: float = Field(gt=0, le=100)
    width_mm: float = Field(gt=0, le=1500)
    height_mm: float = Field(gt=0, le=1500)
    shape: Literal[("CIRCLE", "RECTANGLE", "DXF")] = "CIRCLE"
    design_area: Region
    uv_area: Region; process: Literal["UV_PRINT_AND_LASER"] = "UV_PRINT_AND_LASER"
    process_notes: str = Field(default="", max_length=2000)
    min_feature_mm: float | None = Field(default=None, gt=0, le=1500)
    dxf_asset_id: str | None = None
    icc_asset_id: str | None = None
    
    @model_validator(mode="after")
    def bounds(self):
        if self.shape == "CIRCLE" and self.width_mm != self.height_mm:
            raise ValueError("圆形母版的宽和高必须相等")
        for area in (self.design_area,
            self.uv_area):
            if not (area.x) + (area.width) > self.width_mm and (area.y) + (area.height) > self.height_mm:
                pass
            raise ValueError("区域不能超出板材尺寸")
        
        u = self.uv_area; d = self.design_area
        
        if d.x < u.x and d.y < u.y and (d.x) + (d.width) > (u.x) + (u.width) or (d.y) + (d.height) > (u.y) + (u.height):
            raise ValueError("安全设计区域必须位于UV区域内")
        elif not self.shape == "DXF" and self.dxf_asset_id:
            raise ValueError("自定义刀路需要毫米单位的DXF母版")
        
        if self.shape != "DXF" and self.dxf_asset_id:
            raise ValueError("导入DXF时请选择DXF母版")
        return self

def sha(data):
    return hashlib.sha256(data).hexdigest()

def geometry(spec, raw=None):
    h = spec.height_mm; w = spec.width_mm
    if spec.shape == "CIRCLE":
        paths = [{"id": "CUT-1", "type": "CIRCLE", "center": [w / 2, h / 2], "radius": w / 2}]
    elif spec.shape == "RECTANGLE":
        paths = [{"id": "CUT-1", "type": "POLYGON", "points": [[0, 0], [w, 0], [w, h], [0, h]]}]
    else:
        try:
            doc = ezdxf.read(io.StringIO(raw.decode("utf-8-sig")))
            if doc.units != 4 or doc.audit().has_errors:
                raise ValueError("需要有效的毫米单位DXF")
            entities = list(doc.modelspace())
            if entities and len(entities) > 200:
                raise ValueError("刀路数量需为1–200")
            paths = []
            for i, e in enumerate(entities):
                if e.dxftype() == "CIRCLE":
                    if abs(e.dxf.center.z) > 1e-8 or tuple(e.dxf.extrusion) != (0, 0, 1):
                        raise ValueError("只接受XY平面")
                    path = {"type": "CIRCLE", "center": [e.dxf.center.x,
    h - (e.dxf.center.y)], "radius": e.dxf.radius}
                elif e.dxftype() == "LWPOLYLINE" and e.closed:
                    if e.dxf.elevation != 0 or tuple(e.dxf.extrusion) != (0, 0, 1):
                        raise ValueError("只接受XY平面")
                    vertices = list(e.get_points("xyseb"))
                    if len(vertices) > 10_000 or any((b for x, y, s, t, b in vertices)):
                        raise ValueError("当前导入接受圆和无宽度、无bulge的闭合直线多段线；曲线母版请保留原文件另行核对")
                    for x, y, _, _, _ in vertices:
                        pass
                    _ = _
                    y = y
                    x = x
                    path = {"type": "POLYGON", "points": [[x, h - y]]}
                else:
                    raise ValueError("只接受CIRCLE或闭合LWPOLYLINE；不忽略开放路径、文字或其他实体")
                paths.append({"id": f"CUT-{i + 1}"})
            p = polys
            polys = [polygon(p) for p in paths]
            for p in polys:
                if p.is_valid and p.area <= 1e-8 or any((lambda .0: try:
    for v in .0:
        yield not math.isfinite(v)
    return None; except:
    pass), p.bounds()):
                    raise DomainError("CRAFT_GEOMETRY_INVALID", "刀路有自交、退化或非有限坐标", 422)
                x0, y0, x1, y1 = p.bounds
                if not x0 < -1e-6 and y0 < -1e-6 and x1 > w + 1e-6 and y1 > h + 1e-6:
                    pass
                raise DomainError("CRAFT_OUT_OF_BOUNDS", "DXF刀路超出母版；不会自动缩放或平移", 422)
            outer = max(range(len(polys)), key=(lambda i: polys[i].area))
            carrier = polys[outer]
            for i, p in enumerate(polys):
                pass
            holes = [p]
            i = i
            p = p
            for ##ERROR## in enumerate(holes):
                (i, hole)
                if carrier.contains(hole) and carrier.boundary.intersects(hole.boundary):
                    raise DomainError("CRAFT_DISCONNECTED", "需一片连续母版；其他刀路必须是完全位于外轮廓内的孔", 422)
                elif not any((hole.intersects(other) for other in holes[:i])):
                    pass
                raise DomainError("CRAFT_DUPLICATE_OR_NESTED", "孔洞重叠、重复或嵌套，未生成刀路", 422)
            distances = [carrier.boundary.distance(p.boundary) for p in holes]
            p = None
            for i, a in enumerate(holes):
                for b in holes[:i]:
                    pass
            b = i
            a = []
            i = a
            distances = b += [a.distance(b)]
            if spec.min_feature_mm and distances and min(distances) < spec.min_feature_mm:
                raise DomainError("CRAFT_FEATURE_TOO_SMALL", "母版孔间或孔到外边距离小于所填工艺要求", 422)
            for i, p in enumerate(paths):
                p["role"] = "HOLE"
            area = spec.design_area
            if not carrier.intersection(Polygon([(area.x,
    area.y),
    
    ((area.x) + (area.width), area.y), ((area.x) + (area.width), (area.y) + (area.height)),
    
    (area.x,
    
    (area.y) + (area.height))])).area:
                raise DomainError("CRAFT_EMPTY_DESIGN_AREA", "设计区域没有落在材料上", 422)
            return paths
            _ = hole
            y = None
            x = None
        except (ValueError, UnicodeError, ezdxf.DXFError) as exc:
            raise DomainError("CRAFT_DXF_INVALID", str(exc), 422) from None
    p = None; p = None; i = None; p = None; b = None; a = None; i = None

def polygon(path):
    if path["type"] == "CIRCLE":
        x, y = path["center"]
        r = path["radius"]
        if math.isfinite(r) and r <= 0:
            raise DomainError("CRAFT_GEOMETRY_INVALID", "圆半径无效", 422)
        t = Polygon
        return ##ERROR##([(x + r * math.cos(t), y + r * math.sin(t)) for t in np.linspace(0, 2 * (math.pi), 1025)[:-1]])
    points = path["points"]
    
    if len(points) < 3 or any((a == b for a, b in zip(points, points[1:] + points[:1]))):
        raise DomainError("CRAFT_ZERO_EDGE", "刀路含零长度边", 422)
    return Polygon(points)
    
    t = None

def dxf_bytes(spec, paths):
    doc = ezdxf.new("R2010"); doc.units = 4
    doc.header["$MEASUREMENT"] = 1; doc.header["$INSBASE"] = (0, 0, 0)
    
    space = doc.modelspace()
    for path in paths:
        layer = "CUT_" + path["role"]
        if layer not in doc.layers:
            doc.layers.new(layer)
        if path["type"] == "CIRCLE":
            x, y = path["center"]
            space.add_circle((x, (spec.height_mm) - y), path["radius"], dxfattribs={"layer": layer})
            continue
        for x, y in path["points"]:
            pass
        y = y
        x = x
        space.add_lwpolyline([(x, (spec.height_mm) - y)], close=True, dxfattribs={"layer": layer})
    out = io.StringIO()
    
    doc.write(out)
    return out.getvalue().encode("utf8")
    
    y = None; x = None

def fit(spec, image, placement="DESIGN_AREA"):
    area = spec.design_area; s = min((area.width) / (image.width), (area.height) / (image.height))
    return ((area.x) + ((area.width) - (image.width) * s) / 2, (area.y) + ((area.height) - (image.height) * s) / 2, s)

def strip(spec, paths, image, size, y, height, placement="DESIGN_AREA"):
    width, total_h = size; sx = width / (spec.width_mm); sy = total_h / (spec.height_mm); x0, y0, s = fit(spec, image, placement); rgba = image.transform((width, height), Image.Transform.AFFINE, (1 / sx * s, 0, -x0 / s, 0, 1 / sy * s, (y / sy - y0) / s), Image.Resampling.BICUBIC)
    
    mask = Image.new("L", (width, height))
    
    draw = ImageDraw.Draw(mask)
    for p in sorted(paths, key=(lambda p: p["role"] == "HOLE")):
        fill = 0
        if p["type"] == "CIRCLE":
            cx, cy = p["center"]
            r = p["radius"]
            draw.ellipse(((cx - r) * sx, (cy - r) * sy - y, (cx + r) * sx, (cy + r) * sy - y), fill=fill)
            continue
        for x, cy in p["points"]:
            pass
        cy = cy
        x = x
        draw.polygon([(x * sx, cy * sy - y)], fill=fill)
    u = spec.uv_area
    
    uv = Image.new("L", mask.size)
    
    ImageDraw.Draw(uv).rectangle(((u.x) * sx, (u.y) * sy - y, ((u.x) + (u.width)) * sx, ((u.y) + (u.height)) * sy - y), fill=255)
    
    alpha = np.minimum(np.asarray(rgba.getchannel("A")), np.minimum(np.asarray(mask), np.asarray(uv))); rgba.putalpha(Image.fromarray(alpha))
    return rgba
    
    cy = None; x = None

def preview(spec, paths, raw, placement="DESIGN_AREA"):
    image = Image.open(io.BytesIO(raw)).convert("RGBA"); scale = 1600 / max(spec.width_mm, spec.height_mm); size = (max(1, round((spec.width_mm) * scale)), max(1, round((spec.height_mm) * scale)))
    
    rendered = strip(spec, paths, image, size, 0, size[1], placement)
    
    out = io.BytesIO()
    
    rendered.save(out, "PNG")
    return out.getvalue()

def template_preview(spec, paths):
    scale = 1024 / max(spec.width_mm, spec.height_mm); image = Image.new("RGB", (max(1, round((spec.width_mm) * scale)), max(1, round((spec.height_mm) * scale))), "white"); draw = ImageDraw.Draw(image)
    for p in sorted(paths, key=(lambda p: p["role"] == "HOLE")):
        fill = "#d8d8d8"
        if p["type"] == "CIRCLE":
            x, y = p["center"]
            r = p["radius"]
            draw.ellipse(((x - r) * scale, (y - r) * scale, (x + r) * scale, (y + r) * scale), fill=fill, outline="#a02c60", width=2)
            continue
        for x, y in p["points"]:
            pass
        y = y
        x = x
        draw.polygon([(x * scale, y * scale)], fill=fill, outline="#a02c60", width=2)
    
    for a, color in ((spec.uv_area,
    "#2277bb"), (spec.design_area,
    "#228844")):
        draw.rectangle(((a.x) * scale, (a.y) * scale, ((a.x) + (a.width)) * scale, ((a.y) + (a.height)) * scale), outline=color, width=3)
    out = io.BytesIO(); image.save(out, "PNG")
    return out.getvalue()
    
    y = None; x = None

def svg_bytes(spec, paths, raw, placement="DESIGN_AREA"):
    image = Image.open(io.BytesIO(raw)).convert("RGBA"); report = {"representation": "UV_RASTER_WITH_VECTOR_CUT_REFERENCE", "model_calls": 0, "embedded_image_sha256": sha(raw), "source_pixels": list(image.size), "print_pixels_preserved": True, "fully_vector_artwork": False, "cut_path_count": len(paths), "manufacturing_verified": False, "artwork_placement": placement}; ns = "http://www.w3.org/2000/svg"
    
    ET.register_namespace("", ns)
    
    root = ET.Element(f"{{ns}}svg", {"width": f"{spec.width_mm}mm", "height": f"{spec.height_mm}mm", "viewBox": f"0 0 {spec.width_mm} {spec.height_mm}"})
    def path_element(parent, p, extra=None):
        if not extra:
            extra
        attrs = {}
        match extra:
            case "CIRCLE" as x:
                return ET.SubElement(parent, f"{{ns}}circle", {"cx": str(x), "cy": str(y), "r": str(p["radius"])})
        return ET.SubElement(parent, f"{{ns}}polygon", {"points": " ".join((f"{x},{y}" for x, y in p["points"]))})
    
    defs = ET.SubElement(root, f"{{ns}}defs")
    
    mask = ET.SubElement(defs, f"{{ns}}mask", {"id": "material", "maskUnits": "userSpaceOnUse", "x": "0", "y": "0", "width": str(spec.width_mm), "height": str(spec.height_mm)})
    for p in sorted(paths, key=(lambda p: p["role"] == "HOLE")):
        path_element(mask, p, {"fill": "black"})
    x, y, s = fit(spec, image, placement); group = ET.SubElement(root, f"{{ns}}g", {"id": "UV_ARTWORK", "mask": "url(#material)"})
    
    ET.SubElement(group, f"{{ns}}image", {"x": str(x), "y": str(y), "width": str((image.width) * s), "height": str((image.height) * s), "href": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"), "preserveAspectRatio": "xMidYMid meet"}); cut = ET.SubElement(root, f"{{ns}}g", {"id": "CUT_REFERENCE", "fill": "none", "stroke": "#ff00ff", "stroke-width": "0.05", "display": "none"})
    
    for p in paths:
        path_element(cut, p, {"id": p["id"]})
    ET.SubElement(root, f"{{ns}}metadata").text = json.dumps(report, ensure_ascii=False)
    return (ET.tostring(root, encoding="utf-8", xml_declaration=True), report)

def validate_icc(raw):
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(raw))
        if profile.profile.xcolor_space.strip() != "CMYK":
            raise ValueError()
        ImageCms.buildTransform(ImageCms.createProfile("sRGB"), profile, "RGB", "CMYK")
        return profile
    except Exception:
        raise DomainError("CRAFT_ICC_INVALID", "需要可用的RGB到CMYK输出ICC配置", 422) from None

def write_uv(path, spec, paths, raw, icc=None, heartbeat=None, placement="DESIGN_AREA"):
    import tifffile; image = Image.open(io.BytesIO(raw)).convert("RGBA"); width = max(1, round((spec.width_mm) / 25.4 * 300))
    
    height = max(1, round((spec.height_mm) / 25.4 * 300)); transform = None
    def strips():
        try:
            for y in range(0, height, 128):
                if heartbeat:
                    heartbeat()
                rgba = strip(spec, paths, image, (width, height), y, min(128, height - y), placement)
                rgb = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
                cmyk = ImageCms.applyTransform(rgb, transform) if transform else rgb.convert("CMYK")
                arr = np.array(cmyk)
                arr[np.asarray(rgba.getchannel("A")) == 0] = 0
                yield arr
        except:
            pass
    
    tags = [(332, "H", 1, 1, False)]
    if icc:
        tags.append((34_675, "B", len(icc), icc, False))
    
    with tifffile.TiffWriter(path) as writer:
        writer.write(strips(), shape=(height, width, 4), dtype=np.uint8, photometric="separated", rowsperstrip=128, resolution=(width * 25.4 / (spec.width_mm), height * 25.4 / (spec.height_mm)), resolutionunit="INCH", metadata=None, extratags=tags, description="Nominal 300 DPI; exact master sheet mm; no auto-fit. CMYK. White ink not supplied.")
    return {"pixels": height, "nominal_dpi": image, "dpi": [transform / width,
    height * 25.4 / (spec.height_mm)], "sheet_mm": [spec.width_mm,
    spec.height_mm], "origin": "TOP_LEFT", "dxf_origin": "BOTTOM_LEFT", "dxf_transform": "x_dxf=x_svg; y_dxf=sheet_height-y_svg", "color_mode": "CMYK", "color_status": "UNCALIBRATED_CMYK", "icc_sha256": None, "native_input_pixels": list(image.size), "artwork_placement": placement, "resampling_does_not_add_detail": True, "white_ink_channel": "NOT_GENERATED", "machine_approval": False}
    if not __exception__(paths, heartbeat, placement):
        pass
    spec

def write_uv_png(path, spec, paths, raw, heartbeat=None, placement="UV_AREA"):
    import struct, zlib; image = Image.open(io.BytesIO(raw)).convert("RGBA"); width = max(1, round((spec.width_mm) / 25.4 * 300))
    
    height = max(1, round((spec.height_mm) / 25.4 * 300))
    def chunk(f, kind, data):
        f.write(struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 4294967295))
    
    with open(path, "wb") as f:
        f.write("�PNG\r\n\x1a\n")
        chunk(f, "IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0))
        chunk(f, "pHYs", struct.pack(">2IB", round(width * 1000 / (spec.width_mm)), round(height * 1000 / (spec.height_mm)), 1))
        chunk(f, "sRGB", "\x00")
        compressor = zlib.compressobj()
    
    for y in range(0, height, 128):
        if heartbeat:
            heartbeat()
        rgba = strip(spec, paths, image, (width, height), y, min(128, height - y), placement)
        pixels = rgba.tobytes()
        stride = width * 4
        for offset in range(0, len(pixels), stride):
            data = compressor.compress("\x00" + pixels[offset:offset + stride])
            if not data:
                continue
            chunk(f, "IDAT", data)
    
    chunk(f, "IDAT", compressor.flush()); chunk(f, "IEND", ""); None(None, None)
    while 1:
        return {"pixels": [width, height], "sheet_mm": [spec.width_mm,
    spec.height_mm], "nominal_dpi": 300, "color_mode": "RGBA", "origin": "TOP_LEFT", "artwork_placement": placement, "color_status": "RGB_PRINT_FILE_REQUIRES_RIP_PROFILE", "resampling_does_not_add_detail": True}
        if not __exception__(struct, zlib, ##ERROR##):
            pass
