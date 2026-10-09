"""Two-stage image-to-3D workflow using GPT Image and Tencent HY-3D."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from .config import settings
from .errors import DomainError


SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
VIEW_TYPES = ("front", "left", "right", "back")
GENERATED_VIEW_TYPES = ("left", "right", "back")
VIEW_NAMES = {"front": "正视图", "left": "左视图", "right": "右视图", "back": "后视图"}
SESSION_STATUSES = {
    "PROMPT_REVIEW", "GENERATING_VIEWS", "VIEWS_REVIEW", "CONFIRMED",
    "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED", "FAILED",
}
HUNYUAN_STATUSES = {
    "queued": "GENERATING_3D",
    "in_progress": "GENERATING_3D",
    "completed": "SUCCEEDED",
    "failed": "FAILED",
}
GENERATE_TYPES = {"Normal", "Geometry"}
RESULT_FORMATS = {"", "stl", "usdz", "fbx"}
MODEL_SUFFIXES = {".glb", ".obj", ".zip", ".stl", ".usdz", ".fbx"}

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
    "保持原始主体身份完全一致。只改变摄像机观察角度，不改变主体的几何结构、比例、"
    "零件数量、材质、颜色、纹理、标识或磨损特征。主体完整居中，使用正交产品视图，"
    "纯白背景，均匀中性光照，不添加地面、阴影、文字、水印或原图中不存在的装饰。"
)


class PromptUpdate(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


class ViewGenerationRequest(BaseModel):
    views: list[str] = Field(default_factory=lambda: list(GENERATED_VIEW_TYPES))


class ViewRefinementRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class HunyuanGenerateRequest(BaseModel):
    generate_type: str = "Normal"
    enable_pbr: bool = False
    face_count: int | None = Field(default=None, ge=3000, le=1500000)
    result_format: str = ""


def _provider_error(provider: str, response: httpx.Response) -> DomainError:
    messages = {
        401: f"{provider} API Key 无效",
        402: f"{provider} 余额或积分不足",
        413: f"{provider} 拒绝了过大的文件",
        429: f"{provider} 请求过于频繁，请稍后重试",
    }
    message = messages.get(response.status_code, f"{provider} 请求失败")
    try:
        body = response.json()
        detail = body.get("error", body.get("message", ""))
        if isinstance(detail, dict):
            detail = detail.get("message", detail.get("code", ""))
        if detail:
            message = f"{message}：{str(detail)[:300]}"
    except (ValueError, AttributeError, httpx.ResponseNotRead):
        pass
    return DomainError(f"{provider.upper()}_ERROR", message, 502)


def _normalized_png(data: bytes, declared_type: str | None) -> tuple[bytes, dict]:
    config = settings()
    if len(data) > config.max_upload_bytes:
        raise DomainError("UPLOAD_TOO_LARGE", "图片超过上传限制", 413)
    if declared_type and declared_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise DomainError("UNSUPPORTED_FILE", "仅支持 PNG、JPEG 或 WebP 图片", 415)
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "n_frames", 1) != 1:
                raise ValueError("unsupported image")
            if source.width < 128 or source.height < 128:
                raise DomainError("IMAGE_DIMENSIONS", "图片宽高至少为 128 像素", 413)
            if source.width * source.height > config.max_pixels:
                raise DomainError("IMAGE_DIMENSIONS", "图片像素总数超过限制", 413)
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except DomainError:
        raise
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise DomainError("INVALID_IMAGE", "无法安全解码图片", 415)

    original_size = image.size
    image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    normalized = output.getvalue()
    return normalized, {
        "original_width": original_size[0], "original_height": original_size[1],
        "width": image.width, "height": image.height,
        "sha256": hashlib.sha256(normalized).hexdigest(),
    }


def _validated_generated_png(encoded: str) -> bytes:
    try:
        data = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("unsupported output")
            converted = image.convert("RGBA")
            output = io.BytesIO()
            converted.save(output, "PNG", optimize=True)
            return output.getvalue()
    except (ValueError, OSError, UnidentifiedImageError):
        raise DomainError("OPENAI_INVALID_RESPONSE", "OpenAI 返回的多角度图片无效", 502)


def _data_uri(image: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image).decode("ascii")


def _extract_output_text(body: dict) -> str:
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    raise DomainError("OPENAI_INVALID_RESPONSE", "OpenAI 未返回有效的图片分析结果", 502)


def analyze_image(image_uri: str, user_prompt: str) -> dict:
    config = settings()
    if not config.openai_api_key:
        raise DomainError("OPENAI_NOT_CONFIGURED", "尚未配置 OpenAI API Key", 409)
    instruction = (
        "你正在为单图转多视图再转3D的工作流分析输入图片。请使用中文，只描述可见证据，"
        "将遮挡区域和不可见面的推测明确分开。分析主体、几何结构、材质、颜色、反光、透明、"
        "裁切和复杂背景风险。suggested_prompt_zh 应是用户容易修改的中文主体提示词，"
        "准确概括必须在所有视角保持一致的特征，不要虚构精确尺寸。用户补充要求："
        + (user_prompt.strip() or "忠实还原主体，制作通用3D资产")
    )
    payload = {
        "model": config.openai_vision_model,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": instruction},
            {"type": "input_image", "image_url": image_uri, "detail": "high"},
        ]}],
        "text": {"format": {
            "type": "json_schema", "name": "image_to_3d_analysis", "strict": True,
            "schema": ANALYSIS_SCHEMA,
        }},
        "max_output_tokens": 1600,
    }
    with httpx.Client(base_url=config.openai_base_url, timeout=90) as client:
        response = client.post(
            "/responses", headers={"Authorization": f"Bearer {config.openai_api_key}"}, json=payload,
        )
    if response.status_code >= 400:
        raise _provider_error("OpenAI", response)
    try:
        return json.loads(_extract_output_text(response.json()))
    except (ValueError, TypeError):
        raise DomainError("OPENAI_INVALID_RESPONSE", "OpenAI 图片分析结果格式无效", 502)


def _effective_prompt(user_prompt: str) -> str:
    return f"主体要求：\n{user_prompt.strip()}\n\n所有视角的固定约束：\n{CONSISTENCY_CONSTRAINT}"


def _view_prompt(user_prompt: str, view: str, refinement: str = "") -> str:
    prompt = (
        f"请编辑参考图片，生成同一个主体的{VIEW_NAMES[view]}。\n"
        f"{_effective_prompt(user_prompt)}\n"
        "第一张参考图是原始主体，其他参考图只用于核对跨视角一致性。"
    )
    if refinement:
        prompt += f"\n\n本次仅针对{VIEW_NAMES[view]}的用户修改要求：\n{refinement.strip()}"
    return prompt


def _openai_generate_view(reference_images: list[bytes], prompt: str) -> tuple[bytes, dict]:
    config = settings()
    if not config.openai_api_key:
        raise DomainError("OPENAI_NOT_CONFIGURED", "尚未配置 OpenAI API Key", 409)
    payload = {
        "model": config.openai_image_model,
        "images": [{"image_url": _data_uri(image)} for image in reference_images[:16]],
        "prompt": prompt,
        "quality": config.openai_image_quality,
        "size": "1024x1024",
        "background": "opaque",
        "output_format": "png",
        "moderation": "auto",
        "n": 1,
    }
    with httpx.Client(base_url=config.openai_base_url, timeout=300) as client:
        response = client.post(
            "/images/edits", headers={"Authorization": f"Bearer {config.openai_api_key}"}, json=payload,
        )
    if response.status_code >= 400:
        raise _provider_error("OpenAI", response)
    try:
        body = response.json()
        encoded = body["data"][0]["b64_json"]
        usage = body.get("usage", {})
    except (ValueError, KeyError, IndexError, TypeError):
        raise DomainError("OPENAI_INVALID_RESPONSE", "OpenAI 未返回有效的多角度图片", 502)
    return _validated_generated_png(encoded), usage


def _hunyuan_request(path: str, payload: dict) -> dict:
    config = settings()
    if not config.hunyuan_api_key:
        raise DomainError("HUNYUAN_NOT_CONFIGURED", "尚未配置腾讯混元 TokenHub API Key", 409)
    with httpx.Client(base_url=config.hunyuan_base_url, timeout=120) as client:
        response = client.post(path, headers={
            "Authorization": f"Bearer {config.hunyuan_api_key}", "Content-Type": "application/json",
        }, json=payload)
    if response.status_code >= 400:
        raise _provider_error("Hunyuan", response)
    try:
        body = response.json()
    except ValueError:
        raise DomainError("HUNYUAN_INVALID_RESPONSE", "腾讯混元返回了无法解析的结果", 502)
    if body.get("error"):
        raise DomainError("HUNYUAN_ERROR", f"腾讯混元请求失败：{str(body['error'])[:300]}", 502)
    return body


def _storage_root() -> Path:
    return Path(settings().storage_dir).resolve() / "image-to-3d-sessions"


def _session_dir(session_id: str) -> Path:
    if not SESSION_ID.fullmatch(session_id):
        raise DomainError("INVALID_SESSION_ID", "无效的图生3D会话编号", 404)
    return _storage_root() / session_id


def _record_path(session_id: str) -> Path:
    return _session_dir(session_id) / "session.json"


def _save_record(record: dict) -> None:
    record["updated_at"] = int(time.time())
    path = _record_path(record["session_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _load_record(session_id: str) -> dict:
    try:
        record = json.loads(_record_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DomainError("NOT_FOUND", "图生3D会话不存在或无权访问", 404)
    if record.get("status") not in SESSION_STATUSES:
        raise DomainError("INVALID_SESSION", "图生3D会话状态无效", 500)
    return record


def _owned_record(workspace_id: str, session_id: str) -> dict:
    record = _load_record(session_id)
    if record.get("workspace_id") != workspace_id:
        raise DomainError("NOT_FOUND", "图生3D会话不存在或无权访问", 404)
    return record


def _view_path(record: dict, view: str) -> Path:
    if view not in VIEW_TYPES or view not in record.get("views", {}):
        raise DomainError("VIEW_NOT_FOUND", "多角度图片不存在", 404)
    path = _session_dir(record["session_id"]) / record["views"][view]["filename"]
    if not path.is_file():
        raise DomainError("VIEW_NOT_FOUND", "多角度图片文件不存在", 404)
    return path


def _write_view(record: dict, view: str, data: bytes, *, generated: bool,
                usage: dict | None = None, refinement_prompt: str = "") -> None:
    revision = int(record.get("views", {}).get(view, {}).get("revision", 0)) + 1
    filename = f"views/{view}-r{revision}.png"
    path = _session_dir(record["session_id"]) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    record.setdefault("views", {})[view] = {
        "filename": filename,
        "sha256": hashlib.sha256(data).hexdigest(),
        "revision": revision,
        "prompt_revision": record["prompt_revision"],
        "generated": generated,
        "refinement_prompt": refinement_prompt,
        "usage": usage or {},
    }


def _views_current(record: dict) -> bool:
    views = record.get("views", {})
    if "front" not in views:
        return False
    return all(
        view in views and views[view].get("prompt_revision") == record.get("prompt_revision")
        for view in GENERATED_VIEW_TYPES
    )


def _asset_signature(session_id: str, view: str, expires: int, digest: str) -> str:
    secret = settings().image_to_3d_asset_signing_key
    if not secret:
        raise DomainError("PUBLIC_ASSET_NOT_CONFIGURED", "尚未配置多视角图片临时地址签名密钥", 409)
    message = f"{session_id}:{view}:{expires}:{digest}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _signed_asset_url(record: dict, view: str) -> str:
    base = settings().public_base_url.rstrip("/")
    if not base.startswith("https://"):
        raise DomainError(
            "PUBLIC_URL_NOT_CONFIGURED",
            "腾讯混元多视图生成需要配置外网可访问的 HTTPS ART_PUBLIC_BASE_URL",
            409,
        )
    snapshot = record.get("confirmed_snapshot") or {}
    digest = snapshot.get("views", {}).get(view, {}).get("sha256", "")
    if not digest:
        raise DomainError("NOT_CONFIRMED", "多角度图片尚未确认", 409)
    expires = int(time.time()) + settings().image_to_3d_asset_url_ttl_seconds
    signature = _asset_signature(record["session_id"], view, expires, digest)
    query = urlencode({"expires": expires, "v": digest, "sig": signature})
    return f"{base}/api/image-to-3d/assets/{record['session_id']}/{view}?{query}"


def _snapshot(record: dict) -> dict:
    return {
        "prompt_revision": record["prompt_revision"],
        "user_prompt": record["user_prompt"],
        "effective_prompt": record["effective_prompt"],
        "views": {view: {
            "filename": record["views"][view]["filename"],
            "sha256": record["views"][view]["sha256"],
            "revision": record["views"][view]["revision"],
        } for view in VIEW_TYPES},
        "confirmed_at": int(time.time()),
    }


def _record_response(record: dict) -> dict:
    task = dict(record.get("hunyuan_task") or {})
    downloads = {}
    for model_type, model in (task.get("models") or {}).items():
        if model.get("filename"):
            downloads[model_type] = f"/api/image-to-3d/sessions/{record['session_id']}/models/{model_type}"
    task["downloads"] = downloads
    return {
        "session_id": record["session_id"],
        "status": record["status"],
        "analysis": record.get("analysis"),
        "user_prompt": record.get("user_prompt", ""),
        "effective_prompt": record.get("effective_prompt", ""),
        "prompt_revision": record.get("prompt_revision", 1),
        "views_stale": record.get("views_stale", True),
        "views": {view: {
            key: value for key, value in details.items() if key != "filename"
        } | {"url": f"/api/image-to-3d/sessions/{record['session_id']}/views/{view}"}
            for view, details in record.get("views", {}).items()},
        "confirmed_at": (record.get("confirmed_snapshot") or {}).get("confirmed_at"),
        "hunyuan_task": task or None,
        "last_error": record.get("last_error"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _download_model(url: str, destination: Path) -> None:
    limit = settings().max_model_download_bytes
    temporary = destination.with_suffix(destination.suffix + ".part")
    total = 0
    try:
        with httpx.stream("GET", url, timeout=300, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise _provider_error("Hunyuan", response)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as stream:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise DomainError("MODEL_TOO_LARGE", "腾讯混元模型文件超过本地保存限制", 502)
                    stream.write(chunk)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_hunyuan_results(record: dict, data: list[dict]) -> None:
    models = record.setdefault("hunyuan_task", {}).setdefault("models", {})
    for item in data:
        model_type = str(item.get("type", "")).lower()
        url = item.get("url")
        if not model_type or not isinstance(url, str) or not url.startswith("https://"):
            continue
        current = models.get(model_type, {})
        if current.get("filename") and (_session_dir(record["session_id"]) / current["filename"]).is_file():
            continue
        suffix = Path(urlsplit(url).path).suffix.lower()
        if suffix not in MODEL_SUFFIXES:
            suffix = f".{model_type}" if f".{model_type}" in MODEL_SUFFIXES else ".bin"
        filename = f"models/{model_type}{suffix}"
        _download_model(url, _session_dir(record["session_id"]) / filename)
        models[model_type] = {
            "filename": filename,
            "provider_url": url,
            "preview_image_url": item.get("preview_image_url"),
        }


def build_router(current_dependency: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/image-to-3d", tags=["image-to-3d"])

    @router.get("/options")
    def options(ctx=Depends(current_dependency)):
        return {
            "workflow": "gpt-multiview-to-hunyuan-3d",
            "openai_analysis_model": settings().openai_vision_model,
            "openai_image_model": settings().openai_image_model,
            "openai_image_quality": settings().openai_image_quality,
            "hunyuan_model": settings().hunyuan_3d_model,
            "views": list(VIEW_TYPES),
            "recommended": {"views": list(VIEW_TYPES), "generate_type": "Normal",
                            "enable_pbr": False, "face_count": None},
        }

    @router.post("/sessions")
    def create_session(image: UploadFile = File(...), prompt: str = Form(""),
                       ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        normalized, image_info = _normalized_png(image.file.read(), image.content_type)
        analysis = analyze_image(_data_uri(normalized), prompt)
        user_prompt = prompt.strip() or analysis["suggested_prompt_zh"].strip()
        if not user_prompt:
            raise DomainError("OPENAI_INVALID_RESPONSE", "OpenAI 未生成可用的主体提示词", 502)
        now = int(time.time())
        record = {
            "session_id": uuid.uuid4().hex,
            "workspace_id": workspace.id,
            "status": "PROMPT_REVIEW",
            "analysis": analysis,
            "user_prompt": user_prompt,
            "effective_prompt": _effective_prompt(user_prompt),
            "prompt_revision": 1,
            "views_stale": True,
            "views": {},
            "confirmed_snapshot": None,
            "hunyuan_task": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
            "source_image": image_info,
        }
        _session_dir(record["session_id"]).mkdir(parents=True, exist_ok=True)
        _write_view(record, "front", normalized, generated=False)
        _save_record(record)
        return _record_response(record)

    @router.get("/sessions/{session_id}")
    def get_session(session_id: str, ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        return _record_response(_owned_record(workspace.id, session_id))

    @router.patch("/sessions/{session_id}/prompt")
    def update_prompt(session_id: str, data: PromptUpdate, ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        record = _owned_record(workspace.id, session_id)
        if record["status"] in {"SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED"}:
            raise DomainError("SESSION_LOCKED", "3D任务提交后不能修改提示词", 409)
        record["user_prompt"] = data.prompt.strip()
        record["effective_prompt"] = _effective_prompt(record["user_prompt"])
        record["prompt_revision"] += 1
        record["views_stale"] = True
        record["confirmed_snapshot"] = None
        record["hunyuan_task"] = None
        record["status"] = "PROMPT_REVIEW"
        record["last_error"] = None
        _save_record(record)
        return _record_response(record)

    @router.post("/sessions/{session_id}/views")
    def generate_views(session_id: str, data: ViewGenerationRequest, ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        record = _owned_record(workspace.id, session_id)
        if record["status"] not in {"PROMPT_REVIEW", "VIEWS_REVIEW", "FAILED"}:
            raise DomainError("INVALID_SESSION_STATE", "当前状态不能生成多角度图片", 409)
        requested = list(dict.fromkeys(data.views))
        if not requested or any(view not in GENERATED_VIEW_TYPES for view in requested):
            raise DomainError("INVALID_VIEW", "可生成的视角为 left、right、back", 422)
        record["status"] = "GENERATING_VIEWS"
        record["confirmed_snapshot"] = None
        record["hunyuan_task"] = None
        record["last_error"] = None
        _save_record(record)
        try:
            for view in requested:
                references = [_view_path(record, "front").read_bytes()]
                references.extend(
                    _view_path(record, other).read_bytes()
                    for other in GENERATED_VIEW_TYPES
                    if other != view and other in record.get("views", {})
                    and record["views"][other].get("prompt_revision") == record["prompt_revision"]
                )
                generated, usage = _openai_generate_view(
                    references, _view_prompt(record["user_prompt"], view),
                )
                _write_view(record, view, generated, generated=True, usage=usage)
                _save_record(record)
        except DomainError as exc:
            record["status"] = "VIEWS_REVIEW"
            record["last_error"] = {"code": exc.code, "message": exc.message}
            _save_record(record)
            raise
        record["views_stale"] = not _views_current(record)
        record["status"] = "VIEWS_REVIEW"
        _save_record(record)
        return _record_response(record)

    @router.post("/sessions/{session_id}/views/{view}/refine")
    def refine_view(session_id: str, view: str, data: ViewRefinementRequest,
                    ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        record = _owned_record(workspace.id, session_id)
        if record["status"] != "VIEWS_REVIEW":
            raise DomainError("INVALID_SESSION_STATE", "只有待确认的多角度图片可以微调", 409)
        if view not in GENERATED_VIEW_TYPES or view not in record.get("views", {}):
            raise DomainError("INVALID_VIEW", "只能微调已生成的 left、right 或 back 视图", 422)
        references = [_view_path(record, "front").read_bytes(), _view_path(record, view).read_bytes()]
        references.extend(
            _view_path(record, other).read_bytes()
            for other in GENERATED_VIEW_TYPES if other != view and other in record.get("views", {})
        )
        generated, usage = _openai_generate_view(
            references, _view_prompt(record["user_prompt"], view, data.prompt),
        )
        _write_view(record, view, generated, generated=True, usage=usage,
                    refinement_prompt=data.prompt.strip())
        record["views_stale"] = not _views_current(record)
        record["last_error"] = None
        _save_record(record)
        return _record_response(record)

    @router.post("/sessions/{session_id}/confirm")
    def confirm_views(session_id: str, ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        record = _owned_record(workspace.id, session_id)
        if record["status"] != "VIEWS_REVIEW" or record.get("views_stale"):
            raise DomainError("VIEWS_NOT_READY", "请先生成并检查当前提示词版本的全部四个视角", 409)
        if not _views_current(record):
            raise DomainError("VIEWS_NOT_READY", "多角度图片与当前提示词版本不一致", 409)
        record["confirmed_snapshot"] = _snapshot(record)
        record["status"] = "CONFIRMED"
        record["last_error"] = None
        _save_record(record)
        return _record_response(record)

    @router.post("/sessions/{session_id}/generate")
    def generate_3d(session_id: str, data: HunyuanGenerateRequest, ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        record = _owned_record(workspace.id, session_id)
        if record["status"] != "CONFIRMED" or not record.get("confirmed_snapshot"):
            raise DomainError("NOT_CONFIRMED", "请先确认提示词和全部多角度图片", 409)
        if data.generate_type not in GENERATE_TYPES:
            raise DomainError("INVALID_3D_OPTION", "生成类型只能是 Normal 或 Geometry", 422)
        if data.result_format not in RESULT_FORMATS:
            raise DomainError("INVALID_3D_OPTION", "输出格式只能为空、stl、usdz 或 fbx", 422)
        payload = {
            "model": settings().hunyuan_3d_model,
            "image_url": _signed_asset_url(record, "front"),
            "multi_view_images": [
                {"view_type": view, "view_image_url": _signed_asset_url(record, view)}
                for view in GENERATED_VIEW_TYPES
            ],
            "generate_type": data.generate_type,
            "enable_pbr": data.enable_pbr,
        }
        if data.face_count is not None:
            payload["face_count"] = data.face_count
        if data.result_format:
            payload["result_format"] = data.result_format
        record["status"] = "SUBMITTING_3D"
        record["last_error"] = None
        _save_record(record)
        try:
            created = _hunyuan_request("/api/3d/submit", payload)
        except DomainError as exc:
            record["status"] = "CONFIRMED"
            record["last_error"] = {"code": exc.code, "message": exc.message}
            _save_record(record)
            raise
        task_id = str(created.get("id", ""))
        if not TASK_ID.fullmatch(task_id):
            record["status"] = "FAILED"
            record["last_error"] = {
                "code": "HUNYUAN_INVALID_RESPONSE",
                "message": "腾讯混元已收到请求，但没有返回可查询的任务编号；请勿立即重复提交",
            }
            _save_record(record)
            raise DomainError("HUNYUAN_INVALID_RESPONSE", "腾讯混元未返回有效任务编号", 502)
        record["hunyuan_task"] = {
            "id": task_id, "model": settings().hunyuan_3d_model,
            "status": str(created.get("status", "queued")),
            "request": {
                "generate_type": data.generate_type, "enable_pbr": data.enable_pbr,
                "face_count": data.face_count or "provider-default",
                "result_format": data.result_format or "glb+obj",
            },
            "request_id": created.get("request_id"), "models": {},
        }
        record["status"] = "GENERATING_3D"
        record["last_error"] = None
        _save_record(record)
        return _record_response(record)

    @router.get("/sessions/{session_id}/3d")
    def query_3d(session_id: str, ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        record = _owned_record(workspace.id, session_id)
        task = record.get("hunyuan_task") or {}
        if not TASK_ID.fullmatch(str(task.get("id", ""))):
            raise DomainError("TASK_NOT_CREATED", "腾讯混元3D任务尚未创建", 409)
        result = _hunyuan_request("/api/3d/query", {"model": task["model"], "id": task["id"]})
        provider_status = str(result.get("status", "")).lower()
        if provider_status not in HUNYUAN_STATUSES:
            raise DomainError("HUNYUAN_INVALID_RESPONSE", "腾讯混元返回了未知任务状态", 502)
        task["status"] = provider_status
        task["request_id"] = result.get("request_id", task.get("request_id"))
        task["completed_at"] = result.get("completed_at")
        task["credit_details"] = result.get("result_credit_details")
        record["hunyuan_task"] = task
        record["status"] = HUNYUAN_STATUSES[provider_status]
        if provider_status == "completed":
            try:
                _persist_hunyuan_results(record, result.get("data") or [])
            except DomainError as exc:
                record["last_error"] = {
                    "code": exc.code, "message": "3D已生成，但自动保存模型失败：" + exc.message,
                }
        elif provider_status == "failed":
            record["last_error"] = {
                "code": "HUNYUAN_GENERATION_FAILED",
                "message": str(result.get("error", result.get("message", "腾讯混元生成失败")))[:500],
            }
        _save_record(record)
        return _record_response(record)

    @router.get("/sessions/{session_id}/views/{view}")
    def get_view(session_id: str, view: str, ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        record = _owned_record(workspace.id, session_id)
        return FileResponse(_view_path(record, view), media_type="image/png", filename=f"{view}.png")

    @router.get("/sessions/{session_id}/models/{model_type}")
    def get_model(session_id: str, model_type: str, ctx=Depends(current_dependency)):
        _, _, workspace = ctx
        record = _owned_record(workspace.id, session_id)
        model = ((record.get("hunyuan_task") or {}).get("models") or {}).get(model_type.lower())
        if not model or not model.get("filename"):
            raise DomainError("MODEL_NOT_FOUND", "3D模型文件尚未保存", 404)
        path = _session_dir(session_id) / model["filename"]
        if not path.is_file():
            raise DomainError("MODEL_NOT_FOUND", "3D模型文件不存在", 404)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @router.get("/assets/{session_id}/{view}", include_in_schema=False)
    def public_asset(session_id: str, view: str, expires: int = Query(...),
                     v: str = Query(..., min_length=64, max_length=64),
                     sig: str = Query(..., min_length=64, max_length=64)):
        if view not in VIEW_TYPES or expires < int(time.time()):
            raise DomainError("ASSET_URL_EXPIRED", "多角度图片临时地址无效或已过期", 403)
        expected = _asset_signature(session_id, view, expires, v)
        if not hmac.compare_digest(expected, sig):
            raise DomainError("INVALID_ASSET_SIGNATURE", "多角度图片临时地址签名无效", 403)
        record = _load_record(session_id)
        snapshot = record.get("confirmed_snapshot") or {}
        view_snapshot = snapshot.get("views", {}).get(view) or {}
        if view_snapshot.get("sha256") != v:
            raise DomainError("INVALID_ASSET_VERSION", "多角度图片版本已失效", 403)
        path = _session_dir(session_id) / view_snapshot["filename"]
        if not path.is_file():
            raise DomainError("VIEW_NOT_FOUND", "多角度图片文件不存在", 404)
        return FileResponse(path, media_type="image/png")

    return router
