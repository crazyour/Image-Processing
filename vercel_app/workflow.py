"""Provider calls and image validation for the Vercel image-to-3D app."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from vercel_app.settings import SettingsError, hunyuan_settings, openai_settings


VIEW_TYPES = ("front", "left", "right", "back")
GENERATED_VIEWS = ("left", "right", "back")
VIEW_NAMES = {"front": "正视图", "left": "左视图", "right": "右视图", "back": "背视图"}
GENERATE_TYPES = {"Normal", "Geometry"}
RESULT_FORMATS = {"", "stl", "usdz", "fbx"}
MAX_UPLOAD_BYTES = 4_000_000
MAX_PIXELS = 20_000_000
MAX_MODEL_BYTES = 250_000_000

ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subject": {"type": "string"},
        "geometry": {"type": "string"},
        "materials": {"type": "array", "items": {"type": "string"}},
        "colors": {"type": "array", "items": {"type": "string"}},
        "occlusion_regions": {"type": "array", "items": {"type": "string"}},
        "hidden_geometry_assumptions": {"type": "array", "items": {"type": "string"}},
        "image_suitability": {"type": "string", "enum": ["GOOD", "USABLE", "POOR"]},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "suggested_prompt_zh": {"type": "string"},
    },
    "required": [
        "subject", "geometry", "materials", "colors", "occlusion_regions",
        "hidden_geometry_assumptions", "image_suitability", "warnings", "suggested_prompt_zh",
    ],
}

CONSISTENCY_CONSTRAINT = (
    "保持原始主体身份完全一致。只改变摄像机观察角度，不改变主体几何结构、比例、零件数量、"
    "材质、颜色、纹理、标识或磨损特征。主体完整居中，使用正交产品视图，纯白背景，"
    "均匀中性光照，不增加地面、投影、文字、水印或原图不存在的装饰。"
)


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _openai_config():
    try:
        return openai_settings()
    except SettingsError as exc:
        raise WorkflowError("NOT_CONFIGURED", str(exc), 503) from exc


def _hunyuan_config():
    try:
        return hunyuan_settings()
    except SettingsError as exc:
        raise WorkflowError("NOT_CONFIGURED", str(exc), 503) from exc


def normalize_image(data: bytes, declared_type: str | None) -> tuple[bytes, dict]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise WorkflowError("UPLOAD_TOO_LARGE", "图片不能超过 4 MB", 413)
    if declared_type and declared_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise WorkflowError("UNSUPPORTED_FILE", "仅支持 PNG、JPEG 或 WebP 图片", 415)
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "n_frames", 1) != 1:
                raise ValueError("unsupported image")
            if source.width < 128 or source.height < 128:
                raise WorkflowError("IMAGE_DIMENSIONS", "图片宽高至少为 128 像素", 422)
            if source.width * source.height > MAX_PIXELS:
                raise WorkflowError("IMAGE_DIMENSIONS", "图片像素总数超过限制", 422)
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except WorkflowError:
        raise
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise WorkflowError("INVALID_IMAGE", "无法安全解码图片", 415)
    original = image.size
    image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    normalized = output.getvalue()
    return normalized, {
        "original_width": original[0],
        "original_height": original[1],
        "width": image.width,
        "height": image.height,
        "sha256": hashlib.sha256(normalized).hexdigest(),
    }


def data_uri(image: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image).decode("ascii")


def _provider_error(provider: str, response: httpx.Response) -> WorkflowError:
    labels = {401: "API Key 无效", 402: "余额或积分不足", 413: "文件过大", 429: "请求过于频繁"}
    detail = ""
    try:
        body = response.json()
        detail = body.get("error", body.get("message", ""))
        if isinstance(detail, dict):
            detail = detail.get("message", detail.get("code", ""))
    except (ValueError, AttributeError, httpx.ResponseNotRead):
        pass
    message = f"{provider} {labels.get(response.status_code, '请求失败')}"
    if detail:
        message += f"：{str(detail)[:300]}"
    return WorkflowError(f"{provider.upper()}_ERROR", message, 502)


def _provider_payload_error(provider: str, body: dict) -> WorkflowError:
    """Translate a 2xx business-error envelope without exposing request data."""
    response_body = body.get("Response") if isinstance(body.get("Response"), dict) else {}
    error = body.get("error") or body.get("Error") or response_body.get("Error") or {}
    if not isinstance(error, dict):
        error = {"message": error}
    code = str(
        error.get("code") or error.get("Code") or body.get("error_code")
        or body.get("code") or body.get("status_code") or ""
    ).strip()
    detail = str(
        error.get("message") or error.get("Message") or body.get("error_message")
        or body.get("message") or body.get("status_msg") or ""
    ).strip()
    request_id = str(
        body.get("request_id") or body.get("RequestId") or response_body.get("RequestId") or ""
    ).strip()
    combined = f"{code} {detail}".lower()
    if "balance" in combined or "余额" in combined or "积分不足" in combined:
        summary = "余额或积分不足"
    elif code or detail:
        summary = "请求被拒绝"
    else:
        summary = "返回内容缺少任务编号"
    message = f"{provider}{summary}"
    if code:
        message += f"（{code}）"
    if detail:
        message += f"：{detail[:300]}"
    if request_id:
        message += f"；Request ID：{request_id[:128]}"
    return WorkflowError(f"{provider.upper()}_ERROR", message, 502)


def _extract_output_text(body: dict) -> str:
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    raise WorkflowError("OPENAI_INVALID_RESPONSE", "OpenAI 未返回图片分析结果", 502)


def analyze_image(image: bytes, user_prompt: str, model: str) -> dict:
    config = _openai_config()
    instruction = (
        "你正在为单图转多视图再转3D分析输入图片。请使用中文，只描述可见证据，将遮挡区域"
        "与不可见面的推测分开。分析主体、几何、材质、颜色、反光、透明、裁切和背景风险。"
        "suggested_prompt_zh 是用户可修改的中文主体提示词，描述所有视角必须保持一致的特征。"
        "不要虚构精确尺寸。用户补充："
        + (user_prompt.strip() or "忠实还原主体，制作通用3D资产")
    )
    payload = {
        "model": model,
        "reasoning": {"effort": "none"},
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": instruction},
            {"type": "input_image", "image_url": data_uri(image), "detail": "high"},
        ]}],
        "text": {"format": {
            "type": "json_schema", "name": "image_to_3d_analysis",
            "strict": True, "schema": ANALYSIS_SCHEMA,
        }},
        "max_output_tokens": 1600,
    }
    try:
        with httpx.Client(base_url=config.base_url, timeout=90) as client:
            response = client.post("/responses", headers={"Authorization": f"Bearer {config.api_key}"}, json=payload)
    except httpx.RequestError as exc:
        raise WorkflowError("OPENAI_UNAVAILABLE", f"OpenAI 连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("OpenAI", response)
    try:
        return json.loads(_extract_output_text(response.json()))
    except (ValueError, TypeError):
        raise WorkflowError("OPENAI_INVALID_RESPONSE", "OpenAI 图片分析结果格式无效", 502)


def effective_prompt(user_prompt: str) -> str:
    return f"主体要求：\n{user_prompt.strip()}\n\n跨视角固定约束：\n{CONSISTENCY_CONSTRAINT}"


def view_prompt(user_prompt: str, view: str, refinement: str = "") -> str:
    prompt = (
        f"请编辑参考图片，生成同一个主体的{VIEW_NAMES[view]}。\n"
        f"{effective_prompt(user_prompt)}\n"
        "第一张参考图是原始正面主体，其他参考图只用于核对跨视角一致性。"
    )
    if refinement.strip():
        prompt += f"\n\n本次仅修改{VIEW_NAMES[view]}：\n{refinement.strip()}"
    return prompt


def _validate_generated_image(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            converted = image.convert("RGBA")
            output = io.BytesIO()
            converted.save(output, "PNG", optimize=True)
            return output.getvalue()
    except (ValueError, OSError, UnidentifiedImageError):
        raise WorkflowError("OPENAI_INVALID_RESPONSE", "OpenAI 返回的多视图图片无效", 502)


def generate_view(reference_images: list[bytes], prompt: str, model: str, quality: str) -> tuple[bytes, dict]:
    config = _openai_config()
    payload = {
        "model": model,
        "images": [{"image_url": data_uri(image)} for image in reference_images[:16]],
        "prompt": prompt,
        "quality": quality,
        "size": "1024x1024",
        "background": "opaque",
        "output_format": "png",
        "moderation": "auto",
        "n": 1,
    }
    try:
        with httpx.Client(base_url=config.base_url, timeout=280) as client:
            response = client.post("/images/edits", headers={"Authorization": f"Bearer {config.api_key}"}, json=payload)
    except httpx.RequestError as exc:
        raise WorkflowError("OPENAI_UNAVAILABLE", f"OpenAI 图片生成连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("OpenAI", response)
    try:
        body = response.json()
        image = _validate_generated_image(body["data"][0]["b64_json"])
        return image, body.get("usage", {})
    except (ValueError, KeyError, IndexError, TypeError):
        raise WorkflowError("OPENAI_INVALID_RESPONSE", "OpenAI 未返回有效多视图图片", 502)


def fetch_bytes(url: str, limit: int = MAX_MODEL_BYTES) -> bytes:
    if url.startswith("/local-blobs/"):
        from .store import local_blob_path

        path = local_blob_path(url)
        if path is None or not path.is_file():
            raise WorkflowError("REMOTE_FILE_UNAVAILABLE", "本地文件不存在", 404)
        try:
            if path.stat().st_size > limit:
                raise WorkflowError("REMOTE_FILE_TOO_LARGE", "本地文件超过保存限制", 502)
            return path.read_bytes()
        except OSError as exc:
            raise WorkflowError("REMOTE_FILE_UNAVAILABLE", f"本地文件读取失败：{exc}", 502) from exc

    chunks: list[bytes] = []
    total = 0
    try:
        with httpx.stream("GET", url, timeout=280, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise _provider_error("远程文件", response)
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > limit:
                    raise WorkflowError("REMOTE_FILE_TOO_LARGE", "远程文件超过保存限制", 502)
                chunks.append(chunk)
    except httpx.RequestError as exc:
        raise WorkflowError("REMOTE_FILE_UNAVAILABLE", f"远程文件下载失败：{exc}", 502) from exc
    return b"".join(chunks)


def submit_hunyuan(front_url: str, view_urls: dict[str, str], options: dict, model: str) -> tuple[dict, str]:
    config = _hunyuan_config()
    payload = {
        "model": model,
        "image_url": front_url,
        "multi_view_images": [
            {"view_type": view, "view_image_url": view_urls[view]}
            for view in GENERATED_VIEWS
        ],
        "generate_type": options.get("generate_type", "Normal"),
        "enable_pbr": bool(options.get("enable_pbr", False)),
    }
    if options.get("face_count") is not None:
        payload["face_count"] = int(options["face_count"])
    if options.get("result_format"):
        payload["result_format"] = options["result_format"]
    try:
        with httpx.Client(base_url=config.base_url, timeout=120) as client:
            response = client.post("/api/3d/submit", headers={
                "Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json",
            }, json=payload)
    except httpx.RequestError as exc:
        raise WorkflowError("HUNYUAN_UNAVAILABLE", f"腾讯混元连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("腾讯混元", response)
    try:
        body = response.json()
    except ValueError:
        raise WorkflowError("HUNYUAN_INVALID_RESPONSE", "腾讯混元返回内容无法解析", 502)
    if not isinstance(body, dict) or not body.get("id"):
        raise _provider_payload_error("腾讯混元", body if isinstance(body, dict) else {})
    return body, model


def query_hunyuan(task_id: str, model: str) -> dict:
    config = _hunyuan_config()
    try:
        with httpx.Client(base_url=config.base_url, timeout=120) as client:
            response = client.post("/api/3d/query", headers={
                "Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json",
            }, json={"model": model, "id": task_id})
    except httpx.RequestError as exc:
        raise WorkflowError("HUNYUAN_UNAVAILABLE", f"腾讯混元连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("腾讯混元", response)
    try:
        body = response.json()
    except ValueError:
        raise WorkflowError("HUNYUAN_INVALID_RESPONSE", "腾讯混元返回内容无法解析", 502)
    if not isinstance(body, dict) or (not body.get("status") and (body.get("error") or body.get("Error") or body.get("Response"))):
        raise _provider_payload_error("腾讯混元", body if isinstance(body, dict) else {})
    return body


def model_suffix(url: str, model_type: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".glb", ".obj", ".zip", ".stl", ".usdz", ".fbx"}:
        return suffix
    return f".{model_type.lower()}" if model_type.lower() in {"glb", "obj", "stl", "usdz", "fbx"} else ".bin"
