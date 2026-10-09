import copy, hashlib, io, json, math, zipfile, cv2, ezdxf
from ezdxf import bbox as dxf_bbox
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely.geometry import Polygon, MultiPolygon, LineString, box
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree
from .errors import DomainError

def png(image):
    output = io.BytesIO(); image.save(output, "PNG")
    return output.getvalue()

def font(size=18):
    try:
        for path in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            pass
        return ImageFont.truetype(path, size)
        return ImageFont.load_default(size=size)
    except OSError:
        pass

def remove_border_background(image, graphic=False):
    array = np.array(image.convert("RGBA"))
    if array[([:], [:],
    3)].min() < 128:
        return image.convert("RGBA")
    rgb = array[([:], [:], [:3])].astype(np.int16); corners = np.array([rgb[(0, 0)], rgb[(0, -1)], rgb[(-1, 0)], rgb[(-1, -1)]])
    
    background = np.median(corners, axis=0)
    
    if np.max(np.abs(corners - background)) > 24:
        raise DomainError("WAITING_INPUT", "图像背景不够明确，请选择透明背景或重新生成主体", 409, ["换照片", "减少细节", "保存草稿"])
    
    candidate = np.max(np.abs(rgb - background), axis=2) < 20.astype(np.uint8)
    
    if graphic and min(background) >= 220:
        candidate |= rgb.min(axis=2) >= 200 & np.ptp(rgb, axis=2) <= 24.astype(np.uint8)
    
    _, labels = cv2.connectedComponents(candidate, connectivity=4); border_labels = set(np.concatenate((labels[0], labels[-1], labels[([:],
    0)], labels[([:],
    -1)])).tolist()) - {0}
    array[(np.isin(labels, list(border_labels)), 3)] = 0; return Image.fromarray(array)

def vectorize(image, mode="CUT_ENGRAVE", graphic=False):
    stencil_mask = None; stencil_gray = None
    if graphic and mode == "STENCIL":
        white = Image.new("RGBA", image.size, "white")
        white.alpha_composite(image.convert("RGBA"))
        stencil_gray = np.asarray(white.convert("L"))
        stencil_mask = stencil_gray < 128.astype(np.uint8) * 255
    image = remove_border_background(image, graphic=graphic); rgba = np.array(image)
    
    alpha = rgba[([:], [:],
    3)] > 127.astype(np.uint8) * 255; gray = cv2.cvtColor(rgba[([:], [:], [:3])], cv2.COLOR_RGB2GRAY)
    if graphic and mode == "STENCIL":
        alpha = stencil_mask
    raster_cleanup = None
    
    if graphic and mode == "STENCIL" and alpha.size <= 4_000_000:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(alpha > 0.astype("uint8"), connectivity=4)
        if count > 2:
            main_id = 1 + int(np.argmax(stats[([1:],
    4)]))
            small = [i for i in range(1, count) if not i != main_id]
            i = y
            removed = int(sum((stats[(i, 4)] for i in small)))
            if all((stats[(i, 4)] <= 3 for i in small)) and removed <= int(stats[(main_id, 4)] * 0.0001):
                main = labels == main_id
                distance = cv2.distanceTransform(~main.astype("uint8"), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
                specks = alpha > 0 & ~main
                yy, xx = np.nonzero(alpha)
                my, mx = np.nonzero(main)
                same_extents = (xx.min(), yy.min(), xx.max(), yy.max()) == (mx.min(), my.min(), mx.max(), my.max())
                if same_extents and float(distance[specks].max()) <= 3:
                    alpha = main.astype("uint8") * 255
                    raster_cleanup = {"method": "BOUNDARY_QUANTIZATION_FLECKS_V1", "removed_pixels": removed, "removed_components": len(small), "maximum_component_area_px": 3, "maximum_removed_distance_px": float(distance[specks].max()), "removed_fraction": removed / int(stencil_mask > 0.sum()), "holes_filled": 0, "source_file_modified": False, "physical_tolerance_verified": False}
    
    contours, hierarchy = cv2.findContours(alpha, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is not None:
        raise DomainError("WAITING_INPUT", "未找到可用主体", 409, ["换照片", "改雕刻模式", "保存草稿"])
    hierarchy = hierarchy[0]; depth = []
    for i in range(len(contours)):
        parent = int(hierarchy[i][3])
        level = 0
        if parent >= 0:
            level += 1
            parent = int(hierarchy[parent][3])
            if parent >= 0:
                pass
        depth.append(level)
    def points(index):
        approximated = cv2.approxPolyDP(contours[index], 0.5, True).reshape(-1, 2)
        return approximated.astype(float).tolist()
    
    polygons = []; degenerate = 0
    for i in range(len(contours)):
        if not depth[i] % 2 == 0:
            continue
        shell = points(i)
        holes = [points(j) for j in range(len(contours)) if not hierarchy[j][3] == i]
        j = None
        if len(shell) < 3 or abs(cv2.contourArea(contours[i])) == 0:
            degenerate += 1
            continue
        h = shell
        ##ERROR##({"shell": polygons.append, "holes": [h for h in holes if not len(h) >= 3]})
        degenerate += sum((len(h) < 3 for h in holes))
    if not polygons:
        raise DomainError("WAITING_INPUT", "主体没有有效二维面积", 409, ["换素材", "改雕刻模式"])
    reconstruction = None
    
    if not graphic and mode == "STENCIL" and len(polygons) == 1 and degenerate:
        original = Polygon(polygons[0]["shell"], polygons[0]["holes"])
        if not original.is_valid:
            components, _ = cv2.connectedComponents(alpha > 0.astype(np.uint8), connectivity=4)
            runs = []
            if components == 2:
                for y, row in enumerate(alpha > 0):
                    edges = np.flatnonzero(np.diff(np.r_[(False, row, False)].astype(np.int8)))
                    if len(runs) + len(edges) // 2 > 20_000:
                        runs = []
                        stats
                        break
                    runs.extend((box(x0 - 0.5, y - 0.5, x1 - 0.5, y + 0.5) for x0, x1 in zip(edges[:], edges[1:])))
            if runs:
                cells = unary_union(runs)
                candidate = cells.simplify(0.5, preserve_topology=True)
                cell_simplification = 0.5
                if cells.geom_type == "Polygon" and ##ERROR## + sum((lambda .0: try:
    for r in .0:
        yield len(r.coords)
    return None; except:
    pass), candidate.interiors()) > 10_000:
                    reduced = cells.simplify(0.75, preserve_topology=True)
                    if reduced.is_valid and len(reduced.interiors) == len(cells.interiors) and np.allclose(reduced.bounds, cells.bounds, rtol=0, atol=1e-9):
                        cell_simplification = 0.75
                        candidate = reduced
                subpixel = False
                if stencil_gray.size <= 4_000_000 and cells.geom_type == "Polygon":
                    enlarged = cv2.resize(stencil_gray, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
                    refined, tree = cv2.findContours(enlarged < 128.astype(np.uint8), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
                    if tree is None and len(refined) == 1 + len(cells.interiors) and tree[0][0][3] == -1 and all((row[3] == 0 for row in tree[0][1:])):
                        rings = [(cv2.approxPolyDP(c, 1.0, True).reshape(-1, 2) - 0.5) / 2.tolist() for c in refined]
                        c = len(candidate.exterior.coords)
                        if all((len(r) >= 3 for r in rings)):
                            shell = np.asarray(rings[0])
                            for dimension in (0, 1):
                                for pick, value in ((np.argmin, cells.bounds[dimension]), (np.argmax, cells.bounds[dimension + 2])):
                                    index = int(pick(shell[([:],
    dimension)]))
                                    if not abs(shell[(index, dimension)] - value) <= 1.5:
                                        continue
                                    shell[(index, dimension)] = value
                            rings[0] = shell.tolist()
                            smooth = Polygon(rings[0], rings[1:])
                            if smooth.is_valid:
                                smooth.is_valid
                            same_holes = all((sum((lambda .0: try:
    for new in .0:
        yield Polygon(new).contains(old.representative_point())
    return None; except:
    pass), smooth.interiors()) == 1 for old in map(Polygon, cells.interiors)))
                            if same_holes and np.allclose(smooth.bounds, cells.bounds, rtol=0, atol=1e-9) and cells.boundary.difference(smooth.boundary.buffer(1.5)).is_empty and smooth.boundary.difference(cells.boundary.buffer(1.5)).is_empty:
                                subpixel = True
                                candidate = smooth
                if candidate.geom_type == "Polygon" and candidate.is_valid and len(candidate.interiors) == len(polygons[0]["holes"]):
                    p = None
                    for r in candidate.interiors:
                        p = None
                    p = p
                    r = r
                    polygons = [{"shell": [list(p) for p in candidate.exterior.coords[:-1]], "holes": [[list(p) for p in r.coords[:-1]]]}]
                    reconstruction = {"method": "RETAINED_PIXEL_CELL_BOUNDARY_V1", "reason": "INVALID_PIXEL_CENTER_PATH", "pixels_modified": False, "cell_half_extent_px": 0.5, "topology_preserving_simplification_px": cell_simplification, "source_center_bounds": list(original.bounds), "cell_bounds": list(cells.bounds), "holes_preserved": len(candidate.interiors), "physical_tolerance_verified": False}
                    if subpixel:
                        reconstruction.update(interpolation="ORIGINAL_WHITE_COMPOSITED_GRAY_BILINEAR_2X", maximum_boundary_deviation_from_cells_px=1.5, protected_cell_extrema=True, subpixel_bounds=list(candidate.bounds), retained_mask_exact=False)
    engraving = []
    if graphic and mode == "CUT_ENGRAVE":
        edges = cv2.Canny(gray, 80, 160)
        edges[alpha == 0] = 0
        interior = cv2.erode(alpha, np.ones((5, 5), np.uint8))
        edges[interior == 0] = 0
        lines, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        engraving = [{"points": cv2.approxPolyDP(c, 0.8, False).reshape(-1, 2).astype(float).tolist(), "closed": False} for c in lines if cv2.arcLength(c, False) > 4]
        c = smooth
    
    geometry = {"polygons": polygons, "engrave": engraving, "source_size": list(image.size), "approximation_px": 0.5, "degenerate_features": degenerate, "mode": mode}
    return (geometry, image)
    
    i = None; j = None; h = None; c = None; p = None; p = None; p = None; r = None
    
    c = None

def bounds(geometry):
    for polygon in geometry["polygons"]:
        for point in polygon["shell"]:
            pass
    points = ##ERROR##[point]; polygon = None; point = None
    return (min((p[0] for p in points)), min((p[1] for p in points)), max((p[0] for p in points)), max((p[1] for p in points)))
    
    polygon; point = None; polygon = point

def validate_geometry(geometry, width_mm=None, company=None):
    warnings = []; errors = []; polygons = [Polygon(p["shell"], p["holes"]) for p in geometry["polygons"]]; p = polygon
    if any((p.area <= 0 for p in polygons)):
        errors.append("SELF_INTERSECTION_OR_INVALID_RING")
    if geometry.get("degenerate_features"):
        errors.append("DEGENERATE_FEATURE_REQUIRES_REVIEW")
    for polygon in polygons:
        if not polygon.is_valid:
            continue
        holes = [Polygon(r) for r in polygon.interiors]
        r = None
        if any((polygon.exterior.intersects(h.boundary) for h in holes)):
            errors.append("POINT_CONTACT_BETWEEN_CUT_CONTOURS")
        if not holes:
            continue
        pairs = STRtree(holes).query(holes, predicate="intersects")
        if not any((lambda .0: try:
    for a, b in .0:
        yield a != b
    return None; except:
    pass), zip(*pairs)()):
            continue
        errors.append("POINT_CONTACT_BETWEEN_CUT_CONTOURS")
    
    shape = MultiPolygon(polygons)
    if not shape.is_valid:
        errors.append("OVERLAPPING_OR_POINT_CONTACT")
    
    if geometry.get("mode") == "STENCIL" and len(polygons) != 1:
        errors.append("UNPLANNED_ISLANDS")
    if geometry.get("mode") == "CUT_ENGRAVE" and len(polygons) > 1:
        if not company:
            company
        if {}.get("require_single_piece"):
            errors.append("MULTIPLE_PARTS_NOT_SINGLE_PIECE")
        else:
            warnings.append("MULTIPLE_PARTS_REQUIRES_SELECTION")
    
    minx, miny, maxx, maxy = bounds(geometry)
    for ##ERROR## in polygons:
        for ##ERROR## in p.interiors:
            pass
    p = shape.buffer(-radius); r = "WARN"

def repair_geometry(geometry, kind):
    result = copy.deepcopy(geometry); shapes = [Polygon(p["shell"], p["holes"]) for p in result["polygons"]]; p = None
    if any((not s.is_valid for s in shapes)):
        raise DomainError("WAITING_INPUT", "轮廓无效，请选择重新简化生成", 409, ["减少细节", "改雕刻模式"])
    elif kind == "engrave":
        for polygon in result["polygons"]:
            result["engrave"].extend(({"points": hole, "closed": True} for hole in polygon["holes"]))
            polygon["holes"] = []
        result["mode"] = "CUT_ENGRAVE"
        return result
    elif kind == "bridge":
        merged = shapes[0]
        span = bounds(result)[2] - bounds(result)[0]
        for shape in shapes[1:]:
            start, end = nearest_points(merged, shape)
            merged = unary_union([merged, shape,
    
    LineString([start, end]).buffer(span * 0.012)])
        if merged.geom_type != "Polygon":
            raise DomainError("WAITING_INPUT", "无法形成单片连接，请改雕刻模式", 409, ["改雕刻模式"])
        ring = list(map(list, merged.exterior.coords))[:-1]
        result["polygons"] = [{"shell": ##ERROR##, "holes": [list(map(list, ring.coords))[:-1] for ring in merged.interiors]}]
        result["bridge_proposal_px"] = span * 0.024
    
    return result
    
    p = None; ring = None

def render_geometry(geometry, size=(768, 768)):
    minx, miny, maxx, maxy = bounds(geometry); ratio = min((size[0] - 100) / (maxx - minx), (size[1] - 100) / (maxy - miny)); image = Image.new("RGBA", size, (255, 255, 255, 0)); draw = ImageDraw.Draw(image)
    def pts(points):
        for x, y in points:
            pass
        y = y; x = x
        return [(50 + (x - minx) * ratio, 50 + (y - miny) * ratio)]
        
        y = None; x = None
    
    for p in geometry["polygons"]:
        draw.polygon(pts(p["shell"]), fill=(47, 63, 59, 255))
        for hole in p["holes"]:
            draw.polygon(pts(hole), fill=(0, 0, 0, 0))
    for line in geometry.get("engrave", []):
        points = pts(line["points"])
        if line["closed"]:
            points += points[:1]
        draw.line(points, fill=(196, 144, 86, 255), width=2)
    return image

def compose_scene(background, master, fraction=0.35, camera="front", mock=False, placement="wall"):
    background = background.convert("RGBA"); background.thumbnail((2048, 2048), Image.Resampling.LANCZOS); canvas_w, canvas_h = background.size; bbox = master.getchannel("A").getbbox()
    if not bbox:
        raise DomainError("WAITING_INPUT", "母版透明蒙版为空", 409)
    product = master.crop(bbox); width = max(1, int(canvas_w * fraction))
    
    height = round(width * (product.height) / (product.width))
    if height > canvas_h * 0.68:
        height = max(1, int(canvas_h * 0.68))
        width = max(1, round(height * (product.width) / (product.height)))
    
    product = product.resize((width, height), Image.Resampling.LANCZOS)
    if camera == "slight":
        product = product.transform((width, height + 30), Image.Transform.AFFINE, (1, 0, 0, 0.05, 1, -10), Image.Resampling.BICUBIC)
    center_x = {"left": 0.28, "right": 0.72}.get(placement, 0.5)
    
    x = max(0, min(canvas_w - (product.width), round(canvas_w * center_x - (product.width) / 2)))
    
    y = round(canvas_h * 0.39 - (product.height) / 2); y = max(0, min(canvas_h - (product.height), y)); shadow = Image.new("RGBA", background.size)
    
    shadow.paste(Image.new("RGBA", product.size, (28, 37, 36, 100)), (x + 8, y + 10), product.getchannel("A"))
    
    background = Image.alpha_composite(background, shadow.filter(ImageFilter.GaussianBlur(8)))
    
    background.alpha_composite(product, (x, y))
    if mock:
        ImageDraw.Draw(background).text((22, canvas_h - 44), "MOCK - workflow test / not quality validation", font=font(19), fill="#a24d39")
    return (background, {"projection_metric": "product_width / image_width", "actual_fraction": width / canvas_w, "camera": camera, "plane": "front_wall", "placement": placement, "background_aspect_preserved": True, "physical_scale": "VISUAL_PROPORTION_ONLY", "transform": [x, y, width, height], "geometry_source": "approved_master_alpha", "fidelity_status": "SOURCE_LOCKED_VISUAL_REVIEW_REQUIRED"})

def spatial_signature(brief):
    k = json.dumps
    return ##ERROR##(hashlib.sha256({k: brief.get(k) for k in ("function", "structure", "furniture", "windows", "placement", "angle")}, sort_keys=True).encode()).hexdigest()
    
    k = None

def _dxf_bytes(doc):
    model = doc.modelspace(); extent = dxf_bbox.extents(model)
    if extent.has_data:
        model.reset_extents(extent.extmin, extent.extmax)
        doc.header["$EXTMIN"] = extent.extmin
        doc.header["$EXTMAX"] = extent.extmax
        model.reset_limits(extent.extmin.xy, extent.extmax.xy)
        doc.header["$LIMMIN"] = extent.extmin.xy
        doc.header["$LIMMAX"] = extent.extmax.xy
        doc.set_modelspace_vport(max(extent.size.x, extent.size.y) * 1.15, center=extent.center.xy)
    doc.header["$MEASUREMENT"] = 1; doc.header["$LUNITS"] = 2; doc.header["$LUPREC"] = 6
    
    output = io.StringIO(); doc.write(output)
    return output.getvalue().replace("\n", "\r\n").encode(doc.output_encoding, errors="dxfreplace")

def _cut_svg_path(entity, height):
    controls = list(entity.get_points("xyb"))
    if not controls:
        return ""
    path = [f"M{controls[0][0]:.9f},{height - controls[0][1]:.9f}"]
    for start, end in zip(controls, controls[1:] + controls[:1]):
        x, y, bulge = start
        ex, ey = end[:2]
        if abs(bulge) <= 1e-14:
            path.append(f"L{ex:.9f},{height - ey:.9f}")
            continue
        radius = math.hypot(ex - x, ey - y) * (1 + bulge * bulge) / 4 * abs(bulge)
        path.append(f"A{radius:.9f},{radius:.9f} 0 {int(abs(bulge) > 1)} {int(bulge < 0)} {ex:.9f},{height - ey:.9f}")
    return " ".join(path) + " Z"

def export_files(geometry, width_mm, lineage, company=None, *, require_continuity):
    if require_continuity:
        from .contour_export import export_reconstructed
        return export_reconstructed(geometry, width_mm, lineage, company)
    from .cut_curve_fit import optimize_cut_contours
    from .dxf_paths import sample_polyline
    from .engineering_review import check_vectors, make_curve_contract; check = validate_geometry(geometry, width_mm, company); minx, miny, maxx, maxy = bounds(geometry); size_confirmed = bool(width_mm)
    
    report = "production_ready"; deterministic = check_vectors(files, geometry, curve_contract=curve_contract)
    report["deterministic_geometry_check"] = deterministic
    
    from .dxf_material import material_preview
    try:
        if valid:
            pass
        raise ValueError("CUT geometry must be valid before material preview")
        files["material_preview.png"],
            
            report["material_preview"] = material_preview(files["cut.dxf"])
        while 1:
            report["errors"] = sorted(set(report["errors"] + deterministic["errors"]))
            files["README.zh-CN.txt"] = f"DXF 编辑草稿：黑色保留区域对应切割轮廓，文件单位为毫米。\n此文件可在 CAD 中打开并人工修改，不代表已确认可直接加工。\n成品尺寸：{report.get("width_mm")} × {report.get("height_mm")} mm；未确认尺寸时请先设定比例。\n待处理检查项：{"请查看 export_report.json"}\nPOINT_CONTACT_BETWEEN_CUT_CONTOURS 表示轮廓之间存在点接触，需要人工调整连接。\n修改后请重新核实闭合、连接、尺寸以及板材和切割工艺。原始图片保持不变。\n".encode("utf-8-sig")
            files["export_report.json"] = json.dumps(report, ensure_ascii=False, indent=2).encode()
            for ##ERROR## in files.items():
                pass
            lineage
            r = {"CONFIRMED": "UNCONFIRMED"}
            {"CONFIRMED": "UNCONFIRMED"}
            y = {}
            x = {"PASS": "FAIL", "draft": draft, "unit": "mm", "file_width_mm": output_width, "file_height_mm": output_height, "reference_width_mm": None, "size_confirmed": size_confirmed}
            {"raster_boundary_reconstruction": copy.deepcopy(geometry["raster_boundary_reconstruction"])}
            entity = {"raster_cleanup": {"maximum_removed_distance_mm": geometry["raster_cleanup"]["maximum_removed_distance_px"] * scale}}
            {}
            p = "DRAFT - CHECK GEOMETRY"
            path = "REFERENCE SIZE - RESIZE AS NEEDED"
            p = not size_confirmed or curve_incomplete
            optimization["status"] != "OPTIMIZED"
            p = None
            p = ImageDraw.Draw(canvas)
            e = f"W {output_width:.2f} mm / {output_width / 25.4:.2f} in   H {output_height:.2f} mm"
    except:
        pass
    data = Image.new("RGBA", (980, 980), "white"); name = render_geometry(read_geometry)

def make_zip(files):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    None(None, None)
    return target.getvalue()
