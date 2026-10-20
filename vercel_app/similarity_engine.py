"""Deterministic visual and geometry features for the material search MVP."""
from __future__ import annotations

import io
import math
from collections import Counter
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps


FEATURE_VERSION = "material-search-v1"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {".dxf", ".ai"}


class SimilarityFileError(ValueError):
    pass


def file_kind(filename: str) -> str:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix == ".dxf":
        return "dxf"
    if suffix == ".ai":
        return "illustrator"
    raise SimilarityFileError("仅支持 PNG、JPG、JPEG、WebP、DXF 和 AI 文件")


def _normalise(vector: np.ndarray) -> list[float]:
    vector = np.nan_to_num(vector.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    norm = float(np.linalg.norm(vector))
    if norm:
        vector /= norm
    return [round(float(value), 7) for value in vector]


def _flatten(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, "white")
        canvas.alpha_composite(rgba)
        return canvas.convert("RGB")
    return image.convert("RGB")


def _preview_png(image: Image.Image) -> bytes:
    preview = ImageOps.contain(_flatten(image), (900, 900), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    preview.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _visual_vector(image: Image.Image) -> list[float]:
    rgb_image = _flatten(image)
    width, height = rgb_image.size
    fitted = ImageOps.pad(rgb_image, (128, 128), color="white", method=Image.Resampling.LANCZOS)
    rgb = np.asarray(fitted, dtype=np.uint8)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    histogram = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
    histogram /= max(float(histogram.sum()), 1.0)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    thumbnail = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    edges = cv2.Canny(gray, 60, 160)
    edge_thumbnail = cv2.resize(edges, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    aspect = width / max(height, 1)
    size_features = np.array(
        [min(aspect, 8.0) / 8.0, min(1.0 / max(aspect, 1e-6), 8.0) / 8.0],
        dtype=np.float32,
    )
    return _normalise(np.concatenate([histogram, thumbnail.flatten(), edge_thumbnail.flatten(), size_features]))


def process_image(raw: bytes) -> tuple[bytes, list[float], dict[str, Any]]:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            image = _flatten(source)
    except (OSError, ValueError) as exc:
        raise SimilarityFileError("图片无法读取或已经损坏") from exc
    if image.width < 2 or image.height < 2:
        raise SimilarityFileError("图片尺寸无效")
    return (
        _preview_png(image),
        _visual_vector(image),
        {"width": image.width, "height": image.height, "feature_version": FEATURE_VERSION},
    )


def _dxf_pairs(raw: bytes) -> list[tuple[int, str]]:
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise SimilarityFileError("第一版暂不支持二进制DXF，请另存为ASCII DXF")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise SimilarityFileError("DXF文字编码无法识别") from exc
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if len(lines) < 4:
        raise SimilarityFileError("DXF内容不完整")
    pairs: list[tuple[int, str]] = []
    for index in range(0, len(lines) - 1, 2):
        try:
            code = int(lines[index])
        except ValueError:
            continue
        pairs.append((code, lines[index + 1]))
    if not any(code == 0 and value.upper() == "SECTION" for code, value in pairs):
        raise SimilarityFileError("没有识别到有效的DXF区段")
    return pairs


def _number(values: dict[int, list[str]], code: int, default: float = 0.0) -> float:
    try:
        return float(values.get(code, [str(default)])[0])
    except (TypeError, ValueError):
        return default


def _parse_dxf(raw: bytes) -> tuple[list[dict[str, Any]], Counter[str]]:
    pairs = _dxf_pairs(raw)
    in_entities = False
    chunks: list[tuple[str, list[tuple[int, str]]]] = []
    current_type: str | None = None
    current: list[tuple[int, str]] = []
    for code, value in pairs:
        upper = value.upper()
        if code == 2 and upper == "ENTITIES":
            in_entities = True
            continue
        if in_entities and code == 0 and upper == "ENDSEC":
            if current_type:
                chunks.append((current_type, current))
            break
        if not in_entities:
            continue
        if code == 0:
            if current_type:
                chunks.append((current_type, current))
            current_type, current = upper, []
        elif current_type:
            current.append((code, value))

    primitives: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    legacy_polyline: dict[str, Any] | None = None
    for entity_type, items in chunks:
        counts[entity_type] += 1
        values: dict[int, list[str]] = {}
        for code, value in items:
            values.setdefault(code, []).append(value)
        if entity_type == "POLYLINE":
            if legacy_polyline and len(legacy_polyline["points"]) >= 2:
                primitives.append(legacy_polyline)
            legacy_polyline = {"kind": "poly", "points": [], "closed": int(_number(values, 70)) & 1 == 1}
        elif entity_type == "VERTEX" and legacy_polyline is not None:
            legacy_polyline["points"].append((_number(values, 10), _number(values, 20)))
        elif entity_type == "SEQEND" and legacy_polyline is not None:
            if len(legacy_polyline["points"]) >= 2:
                primitives.append(legacy_polyline)
            legacy_polyline = None
        elif entity_type == "LINE":
            primitives.append({"kind": "line", "points": [(_number(values, 10), _number(values, 20)), (_number(values, 11), _number(values, 21))]})
        elif entity_type in {"LWPOLYLINE", "SPLINE"}:
            xs = values.get(10, [])
            ys = values.get(20, [])
            points = []
            for x, y in zip(xs, ys):
                try:
                    points.append((float(x), float(y)))
                except ValueError:
                    continue
            if len(points) >= 2:
                primitives.append({"kind": "poly", "points": points, "closed": int(_number(values, 70)) & 1 == 1})
        elif entity_type == "CIRCLE":
            primitives.append({"kind": "circle", "center": (_number(values, 10), _number(values, 20)), "radius": abs(_number(values, 40))})
        elif entity_type == "ARC":
            primitives.append({"kind": "arc", "center": (_number(values, 10), _number(values, 20)), "radius": abs(_number(values, 40)), "start": _number(values, 50), "end": _number(values, 51)})
        elif entity_type in {"POINT", "INSERT"}:
            primitives.append({"kind": "point", "center": (_number(values, 10), _number(values, 20))})
    if legacy_polyline and len(legacy_polyline["points"]) >= 2:
        primitives.append(legacy_polyline)
    if not primitives:
        raise SimilarityFileError("DXF中没有识别到可预览的二维图元")
    return primitives, counts


def _primitive_bounds(primitive: dict[str, Any]) -> list[tuple[float, float]]:
    if primitive["kind"] in {"line", "poly"}:
        return primitive["points"]
    if primitive["kind"] in {"circle", "arc"}:
        x, y = primitive["center"]
        radius = primitive["radius"]
        return [(x - radius, y - radius), (x + radius, y + radius)]
    return [primitive["center"]]


def _render_dxf(primitives: list[dict[str, Any]]) -> tuple[Image.Image, dict[str, float]]:
    all_points = [point for primitive in primitives for point in _primitive_bounds(primitive)]
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    width = max(max_x - min_x, 1e-6)
    height = max(max_y - min_y, 1e-6)
    canvas_size, margin = 700, 36
    scale = min((canvas_size - margin * 2) / width, (canvas_size - margin * 2) / height)

    def point(value: tuple[float, float]) -> tuple[float, float]:
        x = margin + (value[0] - min_x) * scale + ((canvas_size - margin * 2) - width * scale) / 2
        y = canvas_size - margin - (value[1] - min_y) * scale - ((canvas_size - margin * 2) - height * scale) / 2
        return x, y

    image = Image.new("RGB", (canvas_size, canvas_size), "white")
    draw = ImageDraw.Draw(image)
    stroke = max(1, round(canvas_size / 350))
    for primitive in primitives:
        kind = primitive["kind"]
        if kind == "line":
            draw.line([point(value) for value in primitive["points"]], fill="#17231e", width=stroke)
        elif kind == "poly":
            points = [point(value) for value in primitive["points"]]
            if primitive.get("closed"):
                points.append(points[0])
            draw.line(points, fill="#17231e", width=stroke, joint="curve")
        elif kind in {"circle", "arc"}:
            cx, cy = primitive["center"]
            radius = primitive["radius"]
            left, bottom = point((cx - radius, cy - radius))
            right, top = point((cx + radius, cy + radius))
            box = [left, top, right, bottom]
            if kind == "circle":
                draw.ellipse(box, outline="#17231e", width=stroke)
            else:
                draw.arc(box, start=-primitive["end"], end=-primitive["start"], fill="#17231e", width=stroke)
        else:
            x, y = point(primitive["center"])
            draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill="#17231e")
    return image, {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y, "width": width, "height": height}


def process_dxf(raw: bytes) -> tuple[bytes, list[float], dict[str, Any]]:
    primitives, counts = _parse_dxf(raw)
    image, bounds = _render_dxf(primitives)
    entity_order = ["LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "SPLINE", "ELLIPSE", "INSERT", "TEXT", "MTEXT", "HATCH"]
    geometry = np.array([math.log1p(counts[name]) for name in entity_order], dtype=np.float32)
    geometry = np.concatenate([
        geometry,
        np.array([
            math.log1p(len(primitives)),
            min(bounds["width"] / max(bounds["height"], 1e-6), 20.0) / 20.0,
            min(bounds["height"] / max(bounds["width"], 1e-6), 20.0) / 20.0,
        ], dtype=np.float32),
    ])
    visual = np.asarray(_visual_vector(image), dtype=np.float32)
    vector = _normalise(np.concatenate([geometry * 2.0, visual]))
    metadata: dict[str, Any] = {
        "feature_version": FEATURE_VERSION,
        "entities": dict(counts),
        "primitive_count": len(primitives),
        "bounds": bounds,
    }
    return _preview_png(image), vector, metadata


def process_illustrator(raw: bytes) -> tuple[bytes, list[float], dict[str, Any]]:
    pdf_offset = raw.find(b"%PDF-")
    if pdf_offset < 0:
        raise SimilarityFileError("该AI文件没有PDF兼容内容，请在Illustrator中启用“创建PDF兼容文件”后重新保存")
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(raw[pdf_offset:])
        if len(document) < 1:
            raise SimilarityFileError("AI文件没有可预览的画板")
        page = document[0]
        image = page.render(scale=2.0).to_pil()
        width, height = image.size
        page.close()
        document.close()
    except SimilarityFileError:
        raise
    except Exception as exc:
        raise SimilarityFileError("AI文件的PDF兼容内容无法解析") from exc
    return (
        _preview_png(image),
        _visual_vector(image),
        {"width": width, "height": height, "pdf_compatible": True, "feature_version": FEATURE_VERSION},
    )


def process_file(filename: str, raw: bytes) -> tuple[str, bytes, list[float], dict[str, Any]]:
    kind = file_kind(filename)
    if kind == "image":
        preview, vector, metadata = process_image(raw)
    elif kind == "dxf":
        preview, vector, metadata = process_dxf(raw)
    else:
        preview, vector, metadata = process_illustrator(raw)
    return kind, preview, vector, metadata


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_array = np.asarray(left, dtype=np.float32)
    right_array = np.asarray(right, dtype=np.float32)
    denominator = float(np.linalg.norm(left_array) * np.linalg.norm(right_array))
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, float(np.dot(left_array, right_array) / denominator)))
