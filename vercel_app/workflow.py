"""Provider calls and image validation for the Vercel image-to-3D app."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from vercel_app.settings import SettingsError, hunyuan_settings, meshy_settings, openai_settings


VIEW_TYPES = ("front", "left", "right", "back")
GENERATED_VIEWS = ("left", "right", "back")
AI_GENERATED_VIEWS = VIEW_TYPES
VIEW_NAMES = {"front": "正视图", "left": "左视图", "right": "右视图", "back": "背视图"}
VIEW_DIRECTIONS = {
    "front": "相机位于主体正前方（0°），朝向主体正面，不得是斜前视图。",
    "left": "相机位于主体自身左侧（相对正面90°），严格左侧面；主体的前方指向画面右侧。",
    "right": "相机位于主体自身右侧（与左视图相反180°），严格右侧面；主体的前方指向画面左侧。",
    "back": "相机位于主体正后方（相对正面180°），显示背部，不得重复正面或斜后视图。",
}
SOURCE_VIEW_NAMES = {
    "FRONT": "正面", "FRONT_THREE_QUARTER": "斜前方", "SIDE": "侧面",
    "BACK": "背面", "TOP": "顶部", "UNKNOWN": "不确定",
}
QUALITY_LEVELS = {"good"}
TRIPO_MODEL = "tripo-3d-3.1"
TRIPO_FORMAT_MODEL = "tripo-3d-format"
MAX_UPLOAD_BYTES = 4_000_000
MAX_PIXELS = 20_000_000
MAX_MODEL_BYTES = 250_000_000
MESHY_BALANCE_CACHE_SECONDS = 30
_meshy_balance_cache: dict = {"expires_at": 0.0, "value": None}

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
        "source_view": {
            "type": "string",
            "enum": ["FRONT", "FRONT_THREE_QUARTER", "SIDE", "BACK", "TOP", "UNKNOWN"],
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "suggested_prompt_zh": {"type": "string"},
    },
    "required": [
        "subject", "geometry", "materials", "colors", "occlusion_regions",
        "hidden_geometry_assumptions", "image_suitability", "source_view", "warnings", "suggested_prompt_zh",
    ],
}

CONSISTENCY_CONSTRAINT = (
    "保持原始主体身份完全一致。只改变摄像机观察角度，不改变主体几何结构、比例、零件数量、"
    "材质、颜色、纹理、标识或磨损特征。主体完整居中，使用正交产品视图，纯白背景，"
    "均匀中性光照，不增加地面、投影、文字、水印或原图不存在的装饰。"
)
PRINT_SCULPTURE_CONSTRAINT = (
    "保留原图配色、材质表现、纹理和风格，不额外改成黑白、白模或黏土效果。"
    "主体身份、姿势、比例和主要轮廓不变；仅将细碎发丝整理为清晰连续的实体发束，"
    "减少穿插和封闭夹缝，适度加厚薄衣摆、细饰品并可靠连接，不能粘合手指或抹成团。"
    "四视图使用同一发束走向、部件和连接布局。完整居中、正交、白背景、中性光；"
    "不新增底座、打印支撑、文字或装饰。"
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


def _meshy_config():
    try:
        return meshy_settings()
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
    error_code = ""
    error_type = ""
    request_id = ""
    try:
        body = response.json()
        response_body = body.get("Response") if isinstance(body.get("Response"), dict) else {}
        error = (
            body.get("error") or body.get("Error") or response_body.get("Error")
            or body.get("detail") or body.get("message") or body.get("Message")
            or body.get("msg") or body.get("error_message") or body.get("status_msg") or ""
        )
        if isinstance(error, dict):
            detail = error.get("message") or error.get("Message") or error.get("code") or error.get("Code") or ""
            error_code = str(error.get("code") or error.get("Code") or "")
            error_type = str(error.get("type") or error.get("Type") or "")
        else:
            detail = error
        error_code = error_code or str(
            body.get("error_code") or body.get("code") or body.get("status_code") or ""
        )
        data_body = body.get("data") if isinstance(body.get("data"), dict) else {}
        request_id = str(
            body.get("request_id") or body.get("RequestId") or response_body.get("RequestId")
            or data_body.get("request_id") or ""
        )
    except (ValueError, AttributeError, httpx.ResponseNotRead):
        # Some gateway errors are returned as plain text instead of JSON.
        detail = response.text.strip()[:300]
    combined = f"{error_code} {error_type} {detail}".lower()
    credit_markers = (
        "no credits remaining", "insufficient_quota", "billing_hard_limit",
        "billing hard limit", "exceeded your current quota", "credit balance",
        "余额不足", "积分不足",
    )
    if provider == "OpenAI" and (
        response.status_code == 402 or any(marker in combined for marker in credit_markers)
    ):
        return WorkflowError(
            "OPENAI_CREDITS_EXHAUSTED",
            "OpenAI API 余额已用完。请管理员在 OpenAI Platform 的 Billing 页面充值，"
            "并确认当前 API Key 所属组织和项目有可用额度。",
            402,
        )
    if provider == "OpenAI" and response.status_code == 429:
        return WorkflowError(
            "OPENAI_RATE_LIMITED",
            "OpenAI API 当前请求过于频繁，请稍后重试。",
            429,
        )
    message = f"{provider} {labels.get(response.status_code, '请求失败')}"
    if detail:
        message += f"：{str(detail)[:300]}"
    if error_code and error_code.lower() not in str(detail).lower():
        message += f"（{error_code}）"
    if request_id:
        message += f"；Request ID：{request_id[:128]}"
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


PRICE_FIELDS = (
    "cost", "cost_usd", "total_cost", "total_cost_usd", "usage_cost", "cost_usd_estimate",
    "price", "total_price", "price_usd", "amount", "amount_usd", "billed", "billing", "usd",
)


def usage_payload(body: dict) -> dict:
    """Keep the provider's usage block together with any price reported beside it.

    Some gateways return the price next to ``usage`` instead of inside it, so the
    whole price block is copied over to make sure nothing is lost.
    """
    usage = body.get("usage")
    payload = dict(usage) if isinstance(usage, dict) else {}
    for field in PRICE_FIELDS:
        if field in body and field not in payload:
            payload[field] = body[field]
    return payload


def analyze_images(images: list[bytes], user_prompt: str, model: str) -> tuple[dict, dict]:
    """Analyze reference images and return the analysis together with token usage."""
    if not images:
        raise WorkflowError("REFERENCE_IMAGE_MISSING", "请至少上传一张参考图", 422)
    config = _openai_config()
    instruction = (
        "你正在为多张参考图转多视图再转3D分析输入图片。请使用中文，第一张是主参考图，"
        "其余图片是同一个对象的补充证据。综合所有图片描述同一个主体；如果图片之间冲突，"
        "明确写入 warnings，不要自行拼凑不同物体。只描述可见证据，将遮挡区域与不可见面的推测分开。"
        "source_view 表示第一张主参考图的视角。分析主体、"
        "几何、材质、颜色、反光、透明、裁切和背景风险。"
        "suggested_prompt_zh 是用户可修改的中文主体提示词，描述所有视角必须保持一致的特征。"
        "不要虚构精确尺寸。用户补充："
        + (user_prompt.strip() or "忠实还原主体，制作通用3D资产")
    )
    payload = {
        "model": model,
        "reasoning": {"effort": "none"},
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": instruction},
            *[
                {"type": "input_image", "image_url": data_uri(image), "detail": "high"}
                for image in images[:6]
            ],
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
        body = response.json()
        return json.loads(_extract_output_text(body)), usage_payload(body)
    except (ValueError, TypeError):
        raise WorkflowError("OPENAI_INVALID_RESPONSE", "OpenAI 图片分析结果格式无效", 502)


def analyze_image(image: bytes, user_prompt: str, model: str) -> tuple[dict, dict]:
    """保留单图调用兼容性。"""
    return analyze_images([image], user_prompt, model)


COLOR_ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "object": {"type": "string"},
        "parts": {"type": "array", "items": {"type": "string"}},
        "materials": {"type": "array", "items": {"type": "string"}},
        "style": {"type": "string"},
        "palette": {"type": "array", "items": {"type": "string"}},
        "coloring_prompt": {"type": "string"},
        "scheme_name": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["object", "parts", "materials", "style", "palette", "coloring_prompt", "scheme_name", "warnings"],
}


def analyze_coloring(images: list[bytes], model: str,
                     previous_scheme: dict | None = None) -> tuple[dict, dict]:
    """Identify a product and propose one concrete, reusable multiview color scheme."""
    if not images:
        raise WorkflowError("REFERENCE_IMAGE_MISSING", "请至少提供一张四视图", 422)
    config = _openai_config()
    instruction = (
        "你是产品配色设计师。输入依次是同一物体的正、左、右、背视图。"
        "只根据可见证据识别物体、部件、风格和材质；无法确定的内容写入 warnings，不要伪造。"
        "提出一套适合该物体且跨视角一致的具体配色，palette 中写颜色名、色值和对应部件。"
        "coloring_prompt 必须明确每个可见部件的颜色、材质和表面处理，同时要求严格保持几何、轮廓、部件数量、构图和纯净背景。"
        "scheme_name 用 2–8 个中文字概括配色，不要包含‘方案’、序号、标点或引号。"
    )
    if previous_scheme:
        previous_name = str(previous_scheme.get("name") or "")[:40]
        previous_palette = previous_scheme.get("palette") or []
        instruction += (
            "\n这是用户明确不满意的上一套配色，只用来排除，不得延用、微调或换名："
            f"\n旧名称：{previous_name or '未命名'}"
            f"\n旧色板：{json.dumps(previous_palette, ensure_ascii=False)[:800]}"
            "\n必须重新选择一套明显不同的颜色配置：改变主色系、辅色系和部件之间的颜色分配，"
            "并返回与新配色匹配的新 scheme_name、palette 和 coloring_prompt。"
        )
    payload = {
        "model": model,
        "reasoning": {"effort": "none"},
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": instruction},
            *[{"type": "input_image", "image_url": data_uri(image), "detail": "high"} for image in images[:4]],
        ]}],
        "text": {"format": {"type": "json_schema", "name": "multiview_coloring", "strict": True,
                            "schema": COLOR_ANALYSIS_SCHEMA}},
        "max_output_tokens": 1400,
    }
    try:
        with httpx.Client(base_url=config.base_url, timeout=90) as client:
            response = client.post("/responses", headers={"Authorization": f"Bearer {config.api_key}"}, json=payload)
    except httpx.RequestError as exc:
        raise WorkflowError("OPENAI_UNAVAILABLE", f"OpenAI 连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("OpenAI", response)
    try:
        body = response.json()
        result = json.loads(_extract_output_text(body))
        result["scheme_name"] = re.sub(r"[\s\\/:*?\"<>|]+", "", result["scheme_name"])[:12] or "AI配色"
        return result, usage_payload(body)
    except (ValueError, TypeError, KeyError):
        raise WorkflowError("OPENAI_INVALID_RESPONSE", "OpenAI 配色分析结果格式无效", 502)


def effective_prompt(user_prompt: str, view_mode: str = "faithful") -> str:
    if view_mode not in {"faithful", "print_sculpture"}:
        raise WorkflowError("INVALID_VIEW_MODE", "四视图模式无效", 422)
    constraint = PRINT_SCULPTURE_CONSTRAINT if view_mode == "print_sculpture" else CONSISTENCY_CONSTRAINT
    return f"主体要求：\n{user_prompt.strip()}\n\n跨视角固定约束：\n{constraint}"


def view_prompt(user_prompt: str, view: str, refinement: str = "", *,
                source_view: str = "UNKNOWN", reference_count: int = 1,
                editing_current: bool = False, view_mode: str = "faithful",
                prior_views: tuple[str, ...] = ()) -> str:
    prompt = (
        f"生成同一主体的一张{VIEW_NAMES[view]}。{VIEW_DIRECTIONS[view]}\n"
        "左右以主体自身为准。相机水平、无俯仰、正交投影；禁止3/4斜视、镜像翻转或拼图。"
        "固定主体姿势和部件位置，只改变相机；目标视角优先于参考图和描述中的画面朝向。\n"
        f"{effective_prompt(user_prompt, view_mode)}\n"
        f"前{reference_count}张是原始主体证据，主图视角为{SOURCE_VIEW_NAMES.get(source_view, '不确定')}，"
        "不默认它是正面。不可见面保守补全。"
    )
    if prior_views:
        labels = "；".join(
            f"第{reference_count + index + 1}张是已生成的{VIEW_NAMES[prior]}"
            for index, prior in enumerate(prior_views)
        )
        prompt += (
            f"\n{labels}。这些生成图仅作跨视角结构参考，原始证据、主体要求和造型模式优先。"
            "沿用已确定的部件数量、形状、连接和位置关系，不照搬参考图的观察角度。"
            "合理遮挡可以使部件不可见，但不得删除部件或为了露出部件改变主体姿势。"
        )
    if editing_current:
        prompt += f"最后一张是待修正的{VIEW_NAMES[view]}，其视角可能有误，不得照搬错误方向。"
    if refinement.strip():
        prompt += f"\n\n本次仅修改{VIEW_NAMES[view]}：\n{refinement.strip()}"
    return prompt


def assembly_plan_prompt(user_prompt: str, refinement: str = "") -> str:
    prompt = (
        "根据输入的正、左、右、背四视图，为同一个主体制作一张横向的切片拼装方案图。\n"
        "将主体拆分为可由平板切割的连续截面片、纵向龙骨和必要连接件，通过相互咬合的槽口组装。\n"
        "输出应像激光切割/CNC 排版预览：白色背景、细灰色轮廓线、各零件不重叠、排列整齐，"
        "清楚显示槽口位置和对称件。不添加色彩、材质渲染、场景、文字、尺寸标注或水印。\n"
        "只能根据四视图推导概念性结构，不要伪造精确工程尺寸。\n"
        f"{effective_prompt(user_prompt)}"
    )
    if refinement.strip():
        prompt += f"\n\n本次切片拼装图的补充要求：\n{refinement.strip()}"
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


def generate_image(prompt: str, model: str, quality: str,
                   size: str = "1024x1024") -> tuple[bytes, dict]:
    """Generate one standalone image from text without holding a database transaction."""
    config = _openai_config()
    payload = {
        "model": model,
        "prompt": prompt.strip(),
        "quality": quality,
        "size": size,
        "background": "opaque",
        "output_format": "png",
        "moderation": "auto",
        "n": 1,
    }
    try:
        with httpx.Client(base_url=config.base_url, timeout=280) as client:
            response = client.post(
                "/images/generations",
                headers={"Authorization": f"Bearer {config.api_key}"},
                json=payload,
            )
    except httpx.RequestError as exc:
        raise WorkflowError("OPENAI_UNAVAILABLE", f"OpenAI 图片生成连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("OpenAI", response)
    try:
        body = response.json()
        item = body["data"][0]
        if item.get("b64_json"):
            image = _validate_generated_image(item["b64_json"])
        elif isinstance(item.get("url"), str) and item["url"].startswith("https://"):
            image = fetch_bytes(item["url"], 25_000_000)
            image, _ = normalize_image(image, None)
        else:
            raise KeyError("image")
        return image, usage_payload(body)
    except (ValueError, KeyError, IndexError, TypeError):
        raise WorkflowError("OPENAI_INVALID_RESPONSE", "OpenAI 未返回有效生成图片", 502)


def generate_view(reference_images: list[bytes], prompt: str, model: str, quality: str,
                  size: str = "1024x1024", mask: bytes | None = None) -> tuple[bytes, dict]:
    config = _openai_config()
    payload = {
        "model": model,
        "images": [{"image_url": data_uri(image)} for image in reference_images[:16]],
        "prompt": prompt,
        "quality": quality,
        "size": size,
        "background": "opaque",
        "output_format": "png",
        "moderation": "auto",
        "n": 1,
    }
    if mask is not None:
        payload["mask"] = {"image_url": data_uri(mask)}
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
        return image, usage_payload(body)
    except (ValueError, KeyError, IndexError, TypeError):
        raise WorkflowError("OPENAI_INVALID_RESPONSE", "OpenAI 未返回有效多视图图片", 502)


def fetch_bytes(url: str, limit: int = MAX_MODEL_BYTES, *, timeout: float = 280,
                max_seconds: float | None = None) -> bytes:
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
    started = time.monotonic()
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise _provider_error("远程文件", response)
            if response.status_code != 200 or response.headers.get("Content-Range"):
                raise WorkflowError("REMOTE_FILE_INCOMPLETE", "远程服务器只返回了部分文件，已停止保存，请重试", 502)
            # iter_bytes decodes compression, while Content-Length describes
            # encoded bytes. Compare lengths only for identity responses.
            content_length = response.headers.get("Content-Length", "")
            expected = int(content_length) if content_length.isdecimal() and not response.headers.get("Content-Encoding") else None
            if expected is not None and expected > limit:
                raise WorkflowError("REMOTE_FILE_TOO_LARGE", "远程文件超过保存限制", 502)
            for chunk in response.iter_bytes():
                if max_seconds is not None and time.monotonic() - started > max_seconds:
                    raise WorkflowError("REMOTE_FILE_TIMEOUT", "文件读取超时，请重试检查", 504)
                total += len(chunk)
                if total > limit:
                    raise WorkflowError("REMOTE_FILE_TOO_LARGE", "远程文件超过保存限制", 502)
                chunks.append(chunk)
            if not total or (expected is not None and total != expected):
                raise WorkflowError("REMOTE_FILE_INCOMPLETE", "远程文件下载不完整，已停止保存，请重试", 502)
    except httpx.RequestError as exc:
        raise WorkflowError("REMOTE_FILE_UNAVAILABLE", f"远程文件下载失败：{exc}", 502) from exc
    return b"".join(chunks)


def _require_fixed_model_quality(options: dict) -> None:
    # Accept the old value only as an input alias; it cannot downgrade provider
    # settings. Missing quality uses the same fixed geometry settings.
    quality = options.get("quality", "good")
    if quality == "normal":
        quality = "good"
    if not isinstance(quality, str) or quality not in QUALITY_LEVELS:
        raise WorkflowError("INVALID_OPTION", "3D 生成参数无效", 422)


def submit_hunyuan(front_url: str, view_urls: dict[str, str], options: dict, model: str) -> tuple[dict, str]:
    _require_fixed_model_quality(options)
    config = _hunyuan_config()
    payload = {
        "model": model,
        "image_url": front_url,
        "multi_view_images": [
            {"view_type": view, "view_image_url": view_urls[view]}
            for view in GENERATED_VIEWS
        ],
        "generate_type": "Geometry",
        "enable_pbr": False,
        "face_count": 1_000_000,
    }
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


def submit_meshy(front_url: str, view_urls: dict[str, str], options: dict,
                 model: str = "meshy-7.1") -> tuple[dict, str]:
    """Submit the confirmed four views to Meshy's native multi-image endpoint."""
    _require_fixed_model_quality(options)
    config = _meshy_config()
    payload: dict = {
        "image_urls": [front_url, *[view_urls[view] for view in GENERATED_VIEWS]],
        "ai_model": model,
        "geometry_resolution": "2k",
        "should_texture": False,
        "enable_pbr": False,
        "should_remesh": False,
        "target_formats": ["stl"],
    }
    try:
        with httpx.Client(base_url=config.base_url, timeout=120) as client:
            response = client.post("/openapi/v1/multi-image-to-3d", headers={
                "Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json",
            }, json=payload)
    except httpx.RequestError as exc:
        raise WorkflowError("MESHY_UNAVAILABLE", f"Meshy 连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("Meshy", response)
    try:
        body = response.json()
    except ValueError:
        raise WorkflowError("MESHY_INVALID_RESPONSE", "Meshy 返回内容无法解析", 502)
    task_id = body.get("result") if isinstance(body, dict) else None
    if not task_id:
        raise _provider_payload_error("Meshy", body if isinstance(body, dict) else {})
    return {"id": task_id, "status": "queued", "raw": body}, model


def submit_tripo(front_url: str, view_urls: dict[str, str], options: dict) -> tuple[dict, str]:
    """Submit a smooth-topology Tripo multiview model through TokenHub."""
    config = _hunyuan_config()
    colored = options.get("generate_type", "Geometry") == "Normal"
    payload = {
        "model": TRIPO_MODEL,
        # TokenHub requires one and only one view field in each array item.
        "inputs": [
            {"front": front_url},
            *[{view: view_urls[view]} for view in GENERATED_VIEWS],
        ],
        "texture": colored,
        "pbr": bool(options.get("enable_pbr", False)) if colored else False,
        "export_uv": colored,
        "texture_alignment": "geometry",
        "smart_low_poly": True,
        "face_limit": 15000,
        "geometry_quality": "standard",
        "negative_prompt": (
            "broken mesh, holes, spikes, rough surface, floating parts, "
            "non-manifold geometry, disconnected parts"
        ),
    }
    try:
        with httpx.Client(base_url=config.base_url, timeout=120) as client:
            response = client.post("/api/3d/submit", headers={
                "Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json",
            }, json=payload)
    except httpx.RequestError as exc:
        raise WorkflowError("TRIPO_UNAVAILABLE", f"Tripo 连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("Tripo", response)
    try:
        body = response.json()
    except ValueError:
        raise WorkflowError("TRIPO_INVALID_RESPONSE", "Tripo 返回内容无法解析", 502)
    if not isinstance(body, dict) or not body.get("id"):
        raise _provider_payload_error("Tripo", body if isinstance(body, dict) else {})
    return body, TRIPO_MODEL


def submit_tripo_stl_conversion(model_url: str) -> tuple[dict, str]:
    config = _hunyuan_config()
    payload = {"model": TRIPO_FORMAT_MODEL, "input": model_url, "format": "STL"}
    try:
        with httpx.Client(base_url=config.base_url, timeout=120) as client:
            response = client.post("/api/3d/submit", headers={
                "Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json",
            }, json=payload)
    except httpx.RequestError as exc:
        raise WorkflowError("TRIPO_FORMAT_UNAVAILABLE", f"Tripo STL 转换连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("Tripo STL 转换", response)
    try:
        body = response.json()
    except ValueError:
        raise WorkflowError("TRIPO_FORMAT_INVALID_RESPONSE", "Tripo STL 转换返回内容无法解析", 502)
    if not isinstance(body, dict) or not body.get("id"):
        raise _provider_payload_error("Tripo STL 转换", body if isinstance(body, dict) else {})
    return body, TRIPO_FORMAT_MODEL


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


def query_meshy(task_id: str, model: str = "meshy-7.1") -> dict:
    """Fetch a Meshy task and normalize it to the app's provider-neutral shape."""
    config = _meshy_config()
    try:
        with httpx.Client(base_url=config.base_url, timeout=120) as client:
            response = client.get(
                f"/openapi/v1/multi-image-to-3d/{task_id}",
                headers={"Authorization": f"Bearer {config.api_key}"},
            )
    except httpx.RequestError as exc:
        raise WorkflowError("MESHY_UNAVAILABLE", f"Meshy 连接失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("Meshy", response)
    try:
        body = response.json()
    except ValueError:
        raise WorkflowError("MESHY_INVALID_RESPONSE", "Meshy 返回内容无法解析", 502)
    if not isinstance(body, dict) or not body.get("status"):
        raise _provider_payload_error("Meshy", body if isinstance(body, dict) else {})
    provider_status = str(body.get("status", "")).upper()
    status = {
        "PENDING": "pending",
        "IN_PROGRESS": "in_progress",
        "SUCCEEDED": "completed",
        "FAILED": "failed",
        "CANCELED": "failed",
    }.get(provider_status, provider_status.lower())
    model_urls = body.get("model_urls") if isinstance(body.get("model_urls"), dict) else {}
    data = [
        {"type": file_type, "url": url, "preview_image_url": body.get("thumbnail_url")}
        for file_type, url in model_urls.items()
        if isinstance(url, str)
    ]
    task_error = body.get("task_error")
    if isinstance(task_error, dict):
        error = task_error.get("message") or task_error.get("code") or "Meshy 生成失败"
    else:
        error = task_error or "Meshy 生成失败"
    return {
        "status": status,
        "progress": body.get("progress"),
        "completed_at": body.get("finished_at"),
        "data": data,
        "error": error if status == "failed" else None,
        "result_credit_details": {"consumed_credits": body.get("consumed_credits")},
    }


def query_meshy_balance() -> dict:
    """Return Meshy credits without exposing the provider key to the browser."""
    now = time.monotonic()
    cached = _meshy_balance_cache.get("value")
    if isinstance(cached, dict) and now < float(_meshy_balance_cache.get("expires_at") or 0):
        return dict(cached)
    config = _meshy_config()
    try:
        with httpx.Client(base_url=config.base_url, timeout=30) as client:
            response = client.get(
                "/openapi/v1/balance",
                headers={"Authorization": f"Bearer {config.api_key}"},
            )
    except httpx.RequestError as exc:
        raise WorkflowError("MESHY_UNAVAILABLE", f"Meshy 积分查询失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise _provider_error("Meshy", response)
    try:
        body = response.json()
    except ValueError:
        raise WorkflowError("MESHY_INVALID_RESPONSE", "Meshy 积分返回内容无法解析", 502)
    if not isinstance(body, dict) or not isinstance(body.get("balance"), (int, float)):
        raise WorkflowError("MESHY_INVALID_RESPONSE", "Meshy 积分返回内容缺少余额", 502)
    result = {
        "balance": int(body["balance"]),
        "monthly_credits": int(body.get("monthly_credits") or 0),
        "permanent_credits": int(body.get("permanent_credits") or 0),
        "monthly_resets_at": body.get("monthly_resets_at"),
    }
    _meshy_balance_cache["value"] = result
    _meshy_balance_cache["expires_at"] = now + MESHY_BALANCE_CACHE_SECONDS
    return dict(result)


def model_suffix(url: str, model_type: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".glb", ".obj", ".zip", ".stl", ".usdz", ".fbx"}:
        return suffix
    return f".{model_type.lower()}" if model_type.lower() in {"glb", "obj", "stl", "usdz", "fbx"} else ".bin"
