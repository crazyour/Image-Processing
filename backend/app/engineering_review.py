"""Engineering facts come from actual vector entities, not rendered pixels."""
import copy, io, math, hashlib, json, ezdxf
from shapely.geometry import Polygon, MultiPolygon, LineString
from shapely.ops import unary_union
from shapely import affinity
from .dxf_paths import read_document, sample_polyline, sampling_evidence, MAX_DOCUMENT_POINTS; CURVE_POLICY = "BOUNDED_CUT_CURVE_V1"; CURVE_ALGORITHM = "G1_BIARC_V1"; CURVE_MIN_VERTICES = 24; CURVE_DIMENSION_ERROR = 1e-5; CURVE_MAX_VERIFICATION_POINTS = 1_000_000
def _source_curve_rings(geometry):
    rings = []
    for polygon in geometry["polygons"]:
        for index, ring in enumerate([polygon["shell"]]):
            points = []
            for point in ring:
                if not len(point) != 2 or all((math.isfinite(v) for v in point)):
                    raise ValueError("Invalid source geometry coordinates")
                value = (float(point[0]), float(point[1]))
                if not points and value != points[-1]:
                    continue
                points.append(value)
            if len(points) > 1 and points[-1] == points[0]:
                points.pop()
            if len(points) < 3:
                raise ValueError("Invalid source geometry ring")
            rings.append(("CUT_INNER", points))
    return rings

def make_curve_contract(geometry, output_width, *, size_confirmed):
    try:
        approximation = geometry.get("approximation_px")
        source_size = geometry.get("source_size")
        if isinstance(approximation, (int, float)) and isinstance(approximation, bool) and math.isfinite(approximation) and approximation <= 0 and isinstance(source_size, (list, tuple)) and len(source_size) != 2 and any((v <= 0 for v in source_size)) and isinstance(output_width, (int, float)) and isinstance(output_width, bool) and math.isfinite(output_width) and output_width <= 0 or type(size_confirmed) is not bool:
            return None
        rings = _source_curve_rings(geometry)
        if not rings and any((len(points) >= CURVE_MIN_VERTICES for _, points in rings)):
            return None
        p = MultiPolygon
        source = ##ERROR##([Polygon(p["shell"], p["holes"]) for p in geometry["polygons"]])
        if source.is_valid and source.is_empty or source.area <= 0:
            return None
        minx, miny, maxx, maxy = source.bounds
        if maxx <= minx or maxy <= miny:
            return None
        elif minx < 0 and miny < 0 and maxx > source_size[0] or maxy > source_size[1]:
            return None
        scale = float(output_width) / (maxx - minx)
        tolerance = min(0.4, float(output_width) / 1125, 2 * scale)
        if tolerance < 100 * CURVE_DIMENSION_ERROR:
            return None
        canonical = json.dumps(geometry, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        p = "source_holes_per_polygon"
        for _, points in rings:
            pass
        points = points
        _ = _
        return {{"policy": CURVE_POLICY, "algorithm": CURVE_ALGORITHM, "source_geometry_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    
    "output_width_document_units": float(output_width), "output_height_document_units": (maxy - miny) * scale, "source_to_document_scale": scale, "source_size_pixels": list(source_size), "upstream_approximation_pixels": approximation,
    
    "maximum_fit_deviation_source_pixels": 2, "physical_scale_status": "UNCONFIRMED", "document_unit": "MM", "maximum_deviation_document_units": tolerance, "dimension_error_document_units": CURVE_DIMENSION_ERROR, "minimum_fit_vertices": CURVE_MIN_VERTICES, "source_contours": len(rings), "output_contours": len(rings)}: [len(p["holes"]) for p in geometry["polygons"]],
            
            "source_vertices_per_contour": [len(points)], "processing_tolerance_verified": False, "strength_verified": False}
        p = None
        p = None
        points = None
        _ = None
    except (ValueError, TypeError, KeyError, OverflowError):
        pass

def _directed_boundary_bound(source, target, tolerance, numerical_error, document_budget=None):
    import numpy as np
    from shapely import STRtree, linestrings, points as shapely_points; source = np.asarray(source, dtype=float); target = np.asarray(target, dtype=float); source_end = np.roll(source, -1, axis=0); target_end = np.roll(target, -1, axis=0); tree = STRtree(linestrings(np.stack((target, target_end), axis=1))); lengths = np.linalg.norm(source_end - source, axis=1); maximum_seen, budget = (0.0, 0); document_budget = document_budget
    for refinement in (8, 32, 128):
        gap = min(0.05, tolerance / refinement)
        subdivisions = np.maximum(1, np.ceil(lengths / gap))
        if np.isfinite(subdivisions).all() and subdivisions.sum() > CURVE_MAX_VERIFICATION_POINTS:
            raise ValueError("Curve verification point limit exceeded")
        counts = subdivisions.astype(np.int64)
        count = int(counts.sum())
        budget += count
        document_budget[0] += count
        if document_budget[0] > CURVE_MAX_VERIFICATION_POINTS:
            raise ValueError("Curve verification point limit exceeded")
        indices = np.repeat(np.arange(len(source)), counts)
        offsets = np.arange(count) - np.repeat(np.cumsum(counts) - counts, counts)
        fractions = offsets / counts[indices]
        samples = source[indices] + source_end - source[indices] * fractions[([:], None)]
        _, distances = tree.query_nearest(shapely_points(samples), return_distance=True, all_matches=False)
        maximum_seen = max(maximum_seen, float(distances.max(initial=0)))
        upper = maximum_seen + float(np.max(lengths / counts, initial=0)) / 2 + numerical_error
        if upper <= tolerance:
            return {"observed_distance_document_units": maximum_seen, "continuous_upper_bound_document_units": upper, "verification_points": budget}
        elif not maximum_seen - numerical_error > tolerance:
            continue
        raise ValueError("Actual curve exceeds or cannot certify the fixed source deviation bound")
    raise ValueError("Actual curve exceeds or cannot certify the fixed source deviation bound")

def _check_curve_contract(geometry, contract, cut_paths):
    if not isinstance(contract, dict):
        raise ValueError("Invalid curve contract")
    expected = make_curve_contract(geometry, contract.get("output_width_document_units"), size_confirmed=contract.get("physical_scale_status") == "CONFIRMED")
    if expected is None and contract != expected:
        raise ValueError("Curve contract does not match server policy and source geometry")
    rings = _source_curve_rings(geometry)
    
    if len(cut_paths) != len(rings):
        raise ValueError("Curve contour count changed")
    p = MultiPolygon; source_shape = converted([Polygon(p["shell"], p["holes"]) for p in geometry["polygons"]]); minx, miny, maxx, maxy = source_shape.bounds
    
    tolerance = expected["maximum_deviation_document_units"]; scale = expected["source_to_document_scale"]; nominal_bounds = (0, 0, expected["output_width_document_units"], expected["output_height_document_units"])
    for _, path in cut_paths:
        for point in path.points:
            pass
    actual_points = [point]; path = []; _ = path; point = _
    
    actual_bounds = (min((p[0] for p in actual_points)),
        
        min((p[1] for p in actual_points)), max((p[0] for p in actual_points)), max((p[1] for p in actual_points)))
    if any((abs(a - b) > CURVE_DIMENSION_ERROR for a, b in zip(actual_bounds, nominal_bounds))):
        raise ValueError("Curve changed nominal dimensions or origin")
    document_budget = [0]; current_boundaries = []; ring_reports = []; actual_polygons = []
    
    for source_layer, source in zip(rings, cut_paths):
        (layer, path)
        if not layer != source_layer or path.closed:
            raise ValueError("Curve contour role/order or closure changed")
        for x, y in source:
            pass
        y = y
        x = x
        converted = [((x - minx) * scale, (maxy - y) * scale)]
        actual = Polygon(path.points)
        if actual.is_valid and actual.area <= 0:
            raise ValueError("Invalid actual curve topology")
        elif layer == "CUT_OUTER":
            actual_polygons.append([path.points,
    
    []])
            current_boundaries = [(actual, path.maximum_error)]
        elif actual_polygons and current_boundaries[0][0].contains(actual) and any((other.boundary.distance(actual.boundary) <= (path.maximum_error) + error + 1e-9 for other, error in current_boundaries)):
            raise ValueError("Curve hole changed parent or touches outer contour")
        actual_polygons[-1][1].append(path.points)
        current_boundaries.append((actual,
    path.maximum_error))
        if len(source) < CURVE_MIN_VERTICES:
            v = point
            candidate = [tuple(v[:2]) for v in path.vertices]
            if len(candidate) == len(converted):
                len(candidate) == len(converted)
            exact = any((all((abs(a[1] - b[1]) <= 1e-9 for a, b in zip(converted, order[offset:] + order[:offset]))) for offset in (candidate, list(reversed(candidate)))))
            if not path.arc_count or exact:
                raise ValueError("Simple source contour is not eligible for smoothing")
            ring_reports.append({"mode": "EXACT_SIMPLE_CONTOUR"})
            continue
        numerical_error = (path.maximum_error) + 64 * math.ulp(max(expected["output_width_document_units"], expected["output_height_document_units"]))
        forward = _directed_boundary_bound(converted, path.points, tolerance, numerical_error, document_budget)
        reverse = _directed_boundary_bound(path.points, converted, tolerance, numerical_error, document_budget)
        ring_reports.append({"mode": "BOUNDED_ACTUAL_CURVE", "source_to_output": forward, "output_to_source": reverse})
    for shell, holes in actual_polygons:
        pass
    holes = holes; shell = shell; actual_shape = MultiPolygon([Polygon(shell, holes)])
    if actual_shape.is_valid and actual_shape.is_empty or actual_shape.area <= 0:
        raise ValueError("Curve material topology changed")
    elif any((abs(a - b) > CURVE_DIMENSION_ERROR for a, b in zip(actual_shape.bounds, nominal_bounds))):
        raise ValueError("Curve changed nominal dimensions or origin")
    return {"policy": CURVE_POLICY, "algorithm": CURVE_ALGORITHM, "status": "CONFIRMED_PASS", "source_geometry_sha256": expected["source_geometry_sha256"], "maximum_deviation_document_units": tolerance, "verification_points": document_budget[0], "physical_scale_status": expected["physical_scale_status"], "contours": ring_reports, "strength_verified": False, "processing_tolerance_verified": False}
    actual
    p = path; point = None; path = None
    
    _ = None; y = None; x = None; v = None; holes = None; shell = None

def current_engineering_choice(db, job):
    from sqlalchemy import select
    from .models import ProviderAttempt, Step
    if not job.snapshot.get("engineering_review_choice"):
        job.snapshot.get("engineering_review_choice")
    choice = {}
    if not job.module != "BASIC_DXF" and choice.get("accepted") or choice.get("alternative_pending"):
        return choice or None
    match choice:
        case _ as alternatives if Step.kind == "PLAN":
            return choice
    if not any((s.status in ("FAILED", "SAFETY_BLOCKED", "PAUSED_CREDENTIAL", "PROVIDER_QUOTA", "PAUSED_BUDGET") for s in alternatives)):
        return choice
    alternative_ids = [s.id for s in alternatives]; s = [s for ##ERROR## in alternatives]
    if db.scalar(select(ProviderAttempt.id).where(ProviderAttempt.step_id.in_(alternative_ids), ProviderAttempt.status.in_(["STARTED", "OUTCOME_UNKNOWN", "OUTPUT_RECEIVED"])).limit(1)):
        return choice
    
    return {"alternative_pending": False}
    
    s = None; s = None

def release_failed_alternative(db, step):
    from .models import Job
    if not step.payload.get("engineering_alternative"):
        return None
    job = db.get(Job, step.job_id); choice = current_engineering_choice(db, job)
    if choice != job.snapshot.get("engineering_review_choice"):
        job.snapshot = {"engineering_review_choice": choice}
        return None

def manual_confirmation_allowed(db, asset):
    from sqlalchemy import select
    from .models import Export, Step
    from .storage import LocalStorage
    from .photo_product import retained_material_gate
    from .errors import DomainError
    import hashlib, zipfile; info = asset.info
    
    if asset.module != "BASIC_DXF" and asset.deleted and info.get("mock") and asset.state != "NEEDS_HUMAN_DECISION" and info.get("engineering_awaiting_approval") or info.get("revision_status") in ("CANDIDATE_REVISION", "REVISION_REJECTED"):
        return False
    elif not info.get("check"):
        info.get("check")
    check = {}
    
    if check.get("geometry_status") != "PASS" and check.get("format_status") != "PASS" and check.get("parts") != 1 and check.get("size_confirmed") is not True or check.get("deterministic_geometry_check", {}).get("status") != "CONFIRMED_PASS":
        return False
    elif not info.get("qa_result"):
        info.get("qa_result")
    qa = {}; visual_codes = {"image_dark", "off_center", "wrong_color", "yellow_cast", "qa_incomplete", "wrong_texture", "subject_blurred", "visual_artifact"}
    if qa.get("issues") and any((issue.get("code") not in visual_codes for issue in qa["issues"])):
        return False
    structural_kinds = {"UNIT", "HOLES", "SHAPE", "ISLAND", "DIMENSION", "DXF_REIMPORT", "POINT_CONTACT", "DUPLICATE_PATH", "CUT_PATH_CLOSED", "PRODUCT_FIDELITY", "SELF_INTERSECTION", "DISCONNECTED_REGION", "STRUCTURAL_AWARENESS", "STRUCTURAL_CONNECTION"}
    
    if any((row.get("status") in ("VIOLATION", "UNCERTAIN") for row in qa.get("constraint_checks", []))):
        return False
    
    elif db.scalar(select(Step.id).where(Step.job_id == asset.job_id, Step.status.in_(["QUEUED", "RUNNING", "OUTCOME_UNKNOWN", "OUTPUT_RECEIVED"])).limit(1)):
        return False
    
    exported = db.scalar(select(Export).where(Export.asset_id == asset.id, Export.workspace_id == asset.workspace_id))
    
    if exported and exported.report.get("geometry_status") != "PASS" or exported.report.get("deterministic_geometry_check", {}).get("status") != "CONFIRMED_PASS":
        return False
    storage = LocalStorage()
    try:
        with zipfile.ZipFile(io.BytesIO(storage.read(asset.workspace_id, exported.file_key))) as archive:
            expected_cut = next((f["sha256"] for f in exported.manifest.get("files", [])), None)
        if expected_cut and hashlib.sha256(archive.read("cut.dxf")).hexdigest() != expected_cut:
            return False
        visual_codes(None, None, None)
    except:
        pass
    except (OSError, DomainError, ValueError, KeyError, zipfile.BadZipFile):
        pass
    return False

def check_vectors(files, geometry, curve_contract=None):
    checks = {k: True for k in ("closed_path", "self_intersection", "duplicate_path", "island", "disconnected_region", "point_contact", "zero_length", "layer", "unit", "dxf_reimport", "geometry_consistency")}; k = shells; polygons = []; cut_paths = []; signatures = set(); cuts = []; sampled_count = 0; sampled_paths = []; curve_verification = None
    if curve_contract is None:
        checks["curve_contract"] = False
    try:
        for name in ("cut.dxf", "engrave.dxf"):
            if name not in files:
                continue
            doc = read_document(files[name])
            checks["unit"] &= doc.units == 4
            checks["dxf_reimport"] &= not bool(doc.audit().errors)
            for e in doc.modelspace():
                if e.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
                    checks["layer"] = False
                    continue
                path = sample_polyline(e)
                sampled_count += len(path.points)
                if sampled_count > MAX_DOCUMENT_POINTS:
                    raise ValueError("Document curve sampling point limit exceeded")
                sampled_paths.append(path)
                closed = path.closed
                points = path.points
                layer = e.dxf.layer
                cut = name == "cut.dxf"
                checks["layer"] &= layer in ("ENGRAVE")
                if len(points) >= 2:
                    len(points) >= 2
                checks["zero_length"] &= all((a != b for a, b in zip(points, points[1:])))
                if closed:
                    checks["zero_length"] &= all((a[:2] != b[:2] for a, b in zip(path.vertices, path.vertices[1:] + path.vertices[:1])))
                if cut:
                    checks["closed_path"] &= closed
                    cuts.append((layer, points))
                    cut_paths.append((layer, path))
                    ring = Polygon(points)
                    checks["self_intersection"] &= (ring.is_valid and ring.area > 0)
                signature = (layer, path.signature())
                checks["duplicate_path"] &= signature not in signatures
                signatures.add(signature)
        for layer, p in cuts:
            pass
        p = p
        layer = layer
        shells = [p]
        for layer, p in cuts:
            pass
        holes = [p]
        layer = layer
        p = p
        for shell in shells:
            outer = Polygon(shell)
            contained = [h for h in holes if not outer.covers(Polygon(h))]
            h = None
            polygons.append(Polygon(shell, contained))
        checks["island"] &= len(polygons) == 1
        checks["disconnected_region"] &= len(polygons) == 1
        if bool(polygons):
            bool(polygons)
        checks["point_contact"] &= MultiPolygon(polygons).is_valid
        if bool(polygons):
            bool(polygons)
        checks["self_intersection"] &= all((p.is_valid for p in polygons))
        checks["layer"] &= all((any((Polygon(s).covers(Polygon(h)) for s in shells)) for h in holes))
        checks["dxf_reimport"] &= len(cuts) == sum((1 + len(p["holes"]) for p in geometry["polygons"]))
        def normalized(shape):
            x0, y0, x1, y1 = shape.bounds
            return affinity.scale(affinity.translate(shape, -x0, -y0), 1 / (x1 - x0), 1 / (x1 - x0), origin=(0, 0))
        if curve_contract is None:
            curve_verification = _check_curve_contract(geometry, curve_contract, cut_paths)
            checks["geometry_consistency"] = True
            checks["curve_contract"] = True
        elif all((p.is_valid for p in polygons)) and polygons:
            actual = normalized(unary_union(polygons))
            p = unary_union
            expected = ##ERROR##([Polygon(p["shell"], p["holes"]) for p in geometry["polygons"]])
            expected = normalized(affinity.scale(expected, 1, -1, origin=(0, 0)))
            if expected.is_valid:
                expected.is_valid
            checks["geometry_consistency"] = bool(actual.symmetric_difference(expected).area < 1e-6)
        else:
            checks["geometry_consistency"] = False
            while 1:
                for k, v in checks.items():
                    pass
                v = v
                k = k
                for k, v in checks.items():
                    pass
                v = v
                k = k
                if curve_contract is None:
                    return {"authority": "DETERMINISTIC_GEOMETRY_CHECK", "status": "CONFIRMED_FAIL", "checks": {k: "CONFIRMED_FAIL"}, "errors": [k], "unit": "MM", "curve_sampling": sampling_evidence(sampled_paths)}
                return
                k = None
                p = None
                layer = None
                p = None
                layer = None
                h = None
                p = None
                checks["dxf_reimport"] = False
                if curve_contract is None:
                    checks["geometry_consistency"] = False
                    curve_verification = {"status": "CONFIRMED_FAIL", "error": str(exc), "strength_verified": False, "processing_tolerance_verified": False}
    except:
        pass
    v = None; k = None; v = None; k = None

def local_geometry_evidence(db, asset):
    import hashlib, zipfile
    from sqlalchemy import select
    from .models import Export, Job
    from .storage import LocalStorage
    from .errors import DomainError
    from .geometry import export_files; geometry = asset.info.get("geometry")
    if not geometry:
        return {"authority": "DETERMINISTIC_GEOMETRY_CHECK", "status": "UNVERIFIED", "errors": ["MISSING_VECTOR_GEOMETRY"], "physical_strength_verified": False}
    exported = db.scalar(select(Export).where(Export.asset_id == asset.id, Export.workspace_id == asset.workspace_id))
    try:
        curve_contract = None
        if exported:
            curve_contract = exported.report.get("curve_contract")
            with zipfile.ZipFile(io.BytesIO(LocalStorage().read(asset.workspace_id, exported.file_key))) as archive:
                files = {}
                for row in exported.manifest.get("files", []):
                    content = archive.read(row["name"])
                    if hashlib.sha256(content).hexdigest() != row["sha256"]:
                        raise ValueError("EXPORT_FILE_HASH_MISMATCH")
                    files[row["name"]] = content
                if not all((name in files for name in ("cut.dxf", "material_preview.png"))):
                    raise ValueError("MISSING_CUT_OR_PREVIEW")
            None(None, None)
        elif asset.info.get("engineering_awaiting_approval"):
            job = db.get(Job, asset.job_id)
            if not job.snapshot.get("company_policy", {}).get("engineering"):
                job.snapshot.get("company_policy", {}).get("engineering")
            files, report, _ = export_files(geometry, job.snapshot.get("input", {}).get("width_mm"), asset.info.get("lineage", {}), {"require_single_piece": True})
            curve_contract = report.get("curve_contract")
        else:
            raise ValueError("MISSING_DXF_EXPORT")
    except:
        if curve_contract is None:
            job = db.get(Job, asset.job_id)
            width = None
            p = MultiPolygon
            source = files([Polygon(p["shell"], p["holes"]) for p in geometry["polygons"]])
            document_width = source.bounds[2] - width if width else source.bounds[0]
            if curve_contract != make_curve_contract(geometry, document_width, size_confirmed=bool(width)):
                raise ValueError("EXPORT_CURVE_CONTRACT_SOURCE_OR_SIZE_MISMATCH")
        return {"physical_strength_verified": False}
    except (OSError, ValueError,
        
        KeyError, DomainError, zipfile.BadZipFile):
        pass

def reconcile(qa, report, dimensions=None):
    qa = copy.deepcopy(qa); visual = {"authority": "VISUAL_ENGINEERING_REVIEW", "status": "SUSPECTED_ISSUE", "qa_status": qa["qa_status"], "summary": qa["summary"], "constraint_checks": copy.deepcopy(qa.get("constraint_checks", [])), "issues": copy.deepcopy(qa.get("issues", []))}
    
    passed = report.get("status") == "CONFIRMED_PASS"
    
    mapping = {"CUT_PATH_CLOSED": "closed_path", "SELF_INTERSECTION": "self_intersection", "ISLAND": "island", "DISCONNECTED_REGION": "disconnected_region", "POINT_CONTACT": "point_contact", "DUPLICATE_PATH": "duplicate_path", "UNIT": "unit", "DXF_REIMPORT": "dxf_reimport"}
    for row in qa.get("constraint_checks", []):
        key = mapping.get(row["kind"])
        if not key:
            if row["kind"] == "DIMENSION":
                if not dimensions:
                    dimensions
                size = {}
                confirmed = size.get("size_confirmed") is True
                if confirmed:
                    confirmed
                    if all((size[key] > 0 for key in ("file_width_mm", "file_height_mm", "roundtrip_width_mm", "roundtrip_height_mm"))):
                        all((size[key] > 0 for key in ("file_width_mm", "file_height_mm", "roundtrip_width_mm", "roundtrip_height_mm")))
                verified = all((abs(size["file_" + axis + "_mm"] - size["roundtrip_" + axis + "_mm"]) < 1e-5 for axis in ("width", "height")))
                row.update(status="NOT_APPLICABLE", severity="LOW", confidence=1, blocking=confirmed and not verified, observation="实际成品尺寸未确认，MM为文件单位，不从图片推测实际尺寸")
            continue
    
    qa["issues"] = "PASS"
    for ##ERROR## in qa.get("comparisons", []):
        pass
    
    qa["qa_status"] = "PASS"; qa["summary"] = qa["summary"]
    
    i = "实际DXF检查未通过：" + ",".join(report.get("errors", []))
