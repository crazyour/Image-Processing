"""Standalone FastAPI entrypoint for Vercel."""
from __future__ import annotations

import hashlib
import re
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vercel_app.store import (
    PublicBlobStore,
    SessionConflict,
    SessionNotFound,
    StoreError,
    create_session,
    local_blob_path,
    load_session,
    save_session,
    storage_status,
)
from vercel_app.workflow import (
    AI_GENERATED_VIEWS,
    GENERATED_VIEWS,
    GENERATE_TYPES,
    VIEW_NAMES,
    WorkflowError,
    analyze_image,
    effective_prompt,
    fetch_bytes,
    generate_view,
    model_suffix,
    normalize_image,
    query_hunyuan,
    submit_hunyuan,
    view_prompt,
)


ROOT = Path(__file__).resolve().parent
ORIGINAL_FRONTEND = ROOT / "frontend_dist"
IMAGE3D_FRONTEND = ROOT / "image3d_frontend"
TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
IMAGE_QUALITIES = {"low", "medium", "high", "xhigh", "max", "auto"}
VALID_STATES = {
    "PROMPT_REVIEW", "GENERATING_VIEWS", "VIEWS_REVIEW", "CONFIRMED",
    "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED", "FAILED",
}
TRANSIENT_STATE_TIMEOUTS = {
    "GENERATING_VIEWS": 330,
    "SUBMITTING_3D": 150,
}

app = FastAPI(title="图片生成 3D", version="1.0.0")


class PromptUpdate(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


class ViewGenerationRequest(BaseModel):
    views: list[str] = Field(default_factory=lambda: list(AI_GENERATED_VIEWS))


class ViewRefinementRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class Generate3DRequest(BaseModel):
    generate_type: str = "Normal"
    enable_pbr: bool = False
    face_count: int | None = Field(default=None, ge=3000, le=1500000)
    result_format: Literal["STL"] = "STL"


@app.exception_handler(WorkflowError)
def workflow_error(_, exc: WorkflowError):
    return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)


@app.exception_handler(SessionNotFound)
def session_not_found(_, exc: SessionNotFound):
    return JSONResponse({"code": "NOT_FOUND", "message": str(exc)}, status_code=404)


@app.exception_handler(SessionConflict)
def session_conflict(_, exc: SessionConflict):
    return JSONResponse({"code": "SESSION_CONFLICT", "message": str(exc)}, status_code=409)


@app.exception_handler(StoreError)
def store_error(_, exc: StoreError):
    return JSONResponse({"code": "STORAGE_ERROR", "message": str(exc)}, status_code=503)


@app.exception_handler(RequestValidationError)
def validation_error(_, exc: RequestValidationError):
    messages = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ())[1:])
        detail = str(error.get("msg", "输入内容无效"))
        messages.append(f"{field}：{detail}" if field else detail)
    return JSONResponse(
        {"code": "INVALID_INPUT", "message": "；".join(messages)[:500]},
        status_code=422,
    )


def _require_token(token: str | None) -> str:
    if not token or len(token) < 32:
        raise SessionNotFound("缺少有效的会话访问令牌")
    return token


def _load(session_id: str, token: str | None) -> tuple[dict, int]:
    state, version = load_session(session_id, _require_token(token))
    if state.get("status") not in VALID_STATES:
        raise WorkflowError("INVALID_SESSION", "会话状态无效", 500)
    timeout = TRANSIENT_STATE_TIMEOUTS.get(state["status"])
    updated_at = int(state.get("updated_at") or state.get("created_at") or 0)
    if timeout and updated_at and int(time.time()) - updated_at > timeout:
        previous_status = state["status"]
        if previous_status == "GENERATING_VIEWS":
            state["status"] = "VIEWS_REVIEW" if len(state.get("views", {})) > 1 else "PROMPT_REVIEW"
            state["last_error"] = {
                "code": "VIEW_GENERATION_TIMEOUT",
                "message": "多视图生成等待超时，已解除锁定；已完成的视图会保留，可重试未完成视图。",
            }
        else:
            state["status"] = "CONFIRMED"
            state["last_error"] = {
                "code": "HUNYUAN_SUBMIT_TIMEOUT",
                "message": "3D 任务提交结果未能确认，已解除锁定。重新提交前请先核对腾讯混元任务，避免重复计费。",
            }
        version = _save(state, version)
    return state, version


def _views_current(state: dict) -> bool:
    views = state.get("views", {})
    return all(
        view in views and views[view].get("prompt_revision") == state.get("prompt_revision")
        for view in AI_GENERATED_VIEWS
    )


def _response(state: dict, *, token: str | None = None) -> dict:
    result = {
        "session_id": state["session_id"],
        "status": state["status"],
        "analysis": state.get("analysis"),
        "user_prompt": state.get("user_prompt", ""),
        "effective_prompt": state.get("effective_prompt", ""),
        "prompt_revision": state.get("prompt_revision", 1),
        "views_stale": state.get("views_stale", True),
        "views": state.get("views", {}),
        "confirmed_at": (state.get("confirmed_snapshot") or {}).get("confirmed_at"),
        "hunyuan_task": state.get("hunyuan_task"),
        "last_error": state.get("last_error"),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "model_settings": state.get("model_settings"),
        "view_source": state.get("view_source", "generated"),
    }
    if token:
        result["access_token"] = token
    return result


def _save(state: dict, version: int) -> int:
    state["updated_at"] = int(time.time())
    return save_session(state["session_id"], state, version)


def _model_settings(vision_model: str, image_model: str,
                    image_quality: str, hunyuan_model: str) -> dict[str, str]:
    models = {
        "vision_model": vision_model.strip(),
        "image_model": image_model.strip(),
        "image_quality": image_quality.strip(),
        "hunyuan_model": hunyuan_model.strip(),
    }
    if any(not MODEL_NAME.fullmatch(models[name]) for name in ("vision_model", "image_model", "hunyuan_model")):
        raise WorkflowError("INVALID_MODEL", "模型名称格式无效", 422)
    if models["image_quality"] not in IMAGE_QUALITIES:
        raise WorkflowError("INVALID_IMAGE_QUALITY", "图片质量设置无效", 422)
    return models


def _session_model_settings(state: dict) -> dict[str, str]:
    models = state.get("model_settings")
    if not isinstance(models, dict):
        raise WorkflowError("MODEL_SETTINGS_MISSING", "旧会话没有模型设置，请重新上传图片", 409)
    return models


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "workflow": "gpt-multiview-to-hunyuan-3d",
        "model_configuration": "per-session-web-settings",
        "storage": storage_status(),
    }


@app.get("/api/bootstrap/status", include_in_schema=False)
def legacy_bootstrap_status():
    return {"required": False, "desktop": False, "compatibility_mode": True}


@app.get("/api/me", include_in_schema=False)
def legacy_cloud_workspace():
    return {
        "user": {"id": "cloud-user", "name": "云端访客", "role": "member"},
        "desktop": False,
        "workspace": {
            "id": "cloud-workspace",
            "name": "灵界云端工作台",
            "learning_enabled": False,
            "personalization_enabled": False,
            "review_position": 0,
            "learned_position": 0,
            "active_snapshot_id": None,
        },
        "assistant": {"id": "cloud-assistant", "name": "灵界助手"},
        "private_workspace": True,
        "key_bound": False,
        "api_setup_required": True,
        "free_services": {},
        "provider": "mock",
        "mode": "CLOUD_COMPATIBILITY",
        "budget_scope": "LOCAL",
        "live_enabled": False,
        "styles": [],
        "compatibility_mode": True,
    }


@app.get("/api/assets", include_in_schema=False)
@app.get("/api/jobs", include_in_schema=False)
@app.get("/api/masters", include_in_schema=False)
@app.get("/api/reviews", include_in_schema=False)
def legacy_empty_collections():
    return []


@app.get("/api/budget", include_in_schema=False)
def legacy_budget():
    return {
        "quota_source": "CLOUD_COMPATIBILITY",
        "limit_micros": 0,
        "held_micros": 0,
        "spent_micros": 0,
        "available_micros": 0,
        "pools": [],
    }


@app.post("/api/sessions")
def new_session(
    image: UploadFile = File(...),
    prompt: str = Form("", max_length=4000),
    view_source: Literal["generated", "uploaded"] = Form("generated"),
    vision_model: str = Form(..., max_length=128),
    image_model: str = Form(..., max_length=128),
    image_quality: str = Form(..., max_length=16),
    hunyuan_model: str = Form(..., max_length=128),
):
    models = _model_settings(vision_model, image_model, image_quality, hunyuan_model)
    normalized, image_info = normalize_image(image.file.read(), image.content_type)
    analysis = analyze_image(normalized, prompt, models["vision_model"]) if view_source == "generated" else {}
    user_prompt = prompt.strip() or analysis.get("suggested_prompt_zh", "").strip()
    if view_source == "uploaded" and not user_prompt:
        user_prompt = "保持用户提交的各视图主体、结构、比例、材质和颜色一致"
    if not user_prompt:
        raise WorkflowError("OPENAI_INVALID_RESPONSE", "没有生成可编辑提示词", 502)

    session_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    reference_url = PublicBlobStore().put(
        f"image3d/{session_id}/reference/source.png", normalized, "image/png",
    )
    initial_views = {}
    if view_source == "uploaded":
        initial_views["front"] = {
            "url": reference_url,
            "sha256": hashlib.sha256(normalized).hexdigest(),
            "revision": 1,
            "prompt_revision": 1,
            "generated": False,
            "uploaded": True,
        }
    now = int(time.time())
    state = {
        "session_id": session_id,
        "status": "PROMPT_REVIEW",
        "analysis": analysis,
        "user_prompt": user_prompt,
        "effective_prompt": effective_prompt(user_prompt),
        "prompt_revision": 1,
        "views_stale": True,
        "views": initial_views,
        "reference_image": {
            "url": reference_url,
            "sha256": hashlib.sha256(normalized).hexdigest(),
        },
        "source_image": image_info,
        "confirmed_snapshot": None,
        "hunyuan_task": None,
        "last_error": None,
        "model_settings": models,
        "view_source": view_source,
        "created_at": now,
        "updated_at": now,
    }
    create_session(session_id, token, state)
    return _response(state, token=token)


@app.post("/api/sessions/{session_id}/views/{view}/upload")
def upload_view(session_id: str, view: str, image: UploadFile = File(...),
                x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    if state.get("view_source") != "uploaded":
        raise WorkflowError("UPLOAD_MODE_REQUIRED", "当前会话使用 AI 生成四视图", 409)
    if state["status"] not in {"PROMPT_REVIEW", "VIEWS_REVIEW", "CONFIRMED", "FAILED"}:
        raise WorkflowError("INVALID_SESSION_STATE", "当前状态不能上传三视图", 409)
    if view not in GENERATED_VIEWS:
        raise WorkflowError("INVALID_VIEW", "可上传视角为 left、right、back", 422)

    normalized, image_info = normalize_image(image.file.read(), image.content_type)
    previous = state["views"].get(view, {})
    revision = int(previous.get("revision", 0)) + 1
    url = PublicBlobStore().put(
        f"image3d/{session_id}/views/{view}-r{revision}.png",
        normalized,
        "image/png",
    )
    state["views"][view] = {
        "url": url,
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "revision": revision,
        "prompt_revision": state["prompt_revision"],
        "generated": False,
        "uploaded": True,
        "source_image": image_info,
    }
    state["status"] = "VIEWS_REVIEW"
    state["views_stale"] = not _views_current(state)
    state["confirmed_snapshot"] = None
    state["hunyuan_task"] = None
    state["last_error"] = None
    _save(state, version)
    return _response(state)


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, x_session_token: str | None = Header(default=None)):
    state, _ = _load(session_id, x_session_token)
    return _response(state)


@app.patch("/api/sessions/{session_id}/prompt")
def update_prompt(session_id: str, data: PromptUpdate,
                  x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    if state["status"] in {"SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED"}:
        raise WorkflowError("SESSION_LOCKED", "3D任务提交后不能修改提示词", 409)
    prompt = data.prompt.strip()
    if prompt == state.get("user_prompt", ""):
        return _response(state)
    state["user_prompt"] = prompt
    state["effective_prompt"] = effective_prompt(state["user_prompt"])
    state["prompt_revision"] += 1
    state["views_stale"] = True
    state["confirmed_snapshot"] = None
    state["hunyuan_task"] = None
    state["last_error"] = None
    state["status"] = "PROMPT_REVIEW"
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/views")
def create_views(session_id: str, data: ViewGenerationRequest,
                 x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    models = _session_model_settings(state)
    if state.get("view_source") != "generated":
        raise WorkflowError("GENERATED_MODE_REQUIRED", "当前会话使用手动上传视图", 409)
    if state["status"] not in {"PROMPT_REVIEW", "VIEWS_REVIEW", "FAILED"}:
        raise WorkflowError("INVALID_SESSION_STATE", "当前状态不能生成多视图", 409)
    requested = list(dict.fromkeys(data.views))
    if not requested or any(view not in AI_GENERATED_VIEWS for view in requested):
        raise WorkflowError("INVALID_VIEW", "可生成视角为 front、left、right、back", 422)
    source_view = (state.get("analysis") or {}).get("source_view")
    if state.get("view_source") == "generated" and source_view in {"SIDE", "BACK", "TOP"}:
        raise WorkflowError(
            "FRONT_VIEW_REQUIRED",
            "当前主图不是正面或斜前方视角，请更换接近正面的图片后再生成四视图",
            422,
        )

    state["status"] = "GENERATING_VIEWS"
    state["confirmed_snapshot"] = None
    state["hunyuan_task"] = None
    state["last_error"] = None
    version = _save(state, version)
    generated_views = {}
    failures: list[tuple[str, Exception]] = []
    try:
        reference = state.get("reference_image") or {}
        reference_url = reference.get("url")
        if not reference_url:
            reference_url = state.get("views", {}).get("front", {}).get("url")
            if reference_url:
                state["reference_image"] = {"url": reference_url}
        if not reference_url:
            raise WorkflowError("REFERENCE_IMAGE_MISSING", "原始参考图不存在，请重新上传", 409)
        source = fetch_bytes(reference_url, 20_000_000)
        with ThreadPoolExecutor(max_workers=min(4, len(requested))) as executor:
            futures = {
                executor.submit(
                    generate_view,
                    [source],
                    view_prompt(state["user_prompt"], view),
                    models["image_model"],
                    models["image_quality"],
                ): view
                for view in requested
            }
            for future in as_completed(futures):
                view = futures[future]
                try:
                    generated_views[view] = future.result()
                except Exception as exc:  # retain other paid results when one view fails
                    failures.append((view, exc))
    except Exception as exc:
        failures.append(("all", exc))

    if generated_views:
        try:
            blobs = PublicBlobStore()
            for view, (generated, usage) in generated_views.items():
                previous = state["views"].get(view, {})
                revision = int(previous.get("revision", 0)) + 1
                url = blobs.put(
                    f"image3d/{session_id}/views/{view}-r{revision}.png",
                    generated,
                    "image/png",
                )
                state["views"][view] = {
                    "url": url,
                    "sha256": hashlib.sha256(generated).hexdigest(),
                    "revision": revision,
                    "prompt_revision": state["prompt_revision"],
                    "generated": True,
                    "refinement_prompt": "",
                    "usage": usage,
                }
        except Exception as exc:
            failures.append(("storage", exc))

    state["views_stale"] = not _views_current(state)
    state["status"] = "VIEWS_REVIEW"
    if failures:
        failed_names = "、".join(
            VIEW_NAMES.get(view, "视图") for view, _ in failures if view not in {"all", "storage"}
        )
        first_error = failures[0][1]
        message = str(first_error) if isinstance(first_error, (WorkflowError, StoreError)) else "多视图生成发生意外错误"
        if failed_names:
            message = f"{failed_names}生成失败：{message}；其他已完成视图已保留"
        state["last_error"] = {
            "code": getattr(first_error, "code", "VIEW_GENERATION_FAILED"),
            "message": message,
        }
    else:
        state["last_error"] = None
    _save(state, version)
    if failures:
        first_error = failures[0][1]
        raise WorkflowError(
            state["last_error"]["code"],
            state["last_error"]["message"],
            first_error.status if isinstance(first_error, WorkflowError) else 502,
        )
    return _response(state)


@app.post("/api/sessions/{session_id}/views/{view}/refine")
def refine_view(session_id: str, view: str, data: ViewRefinementRequest,
                x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    models = _session_model_settings(state)
    if state["status"] != "VIEWS_REVIEW":
        raise WorkflowError("INVALID_SESSION_STATE", "只有待确认图片可以微调", 409)
    if (state.get("view_source") != "generated" or view not in AI_GENERATED_VIEWS
            or view not in state.get("views", {})):
        raise WorkflowError("INVALID_VIEW", "只能微调已由 AI 生成的正、左、右或背视图", 422)
    reference_url = (state.get("reference_image") or {}).get("url")
    if not reference_url:
        raise WorkflowError("REFERENCE_IMAGE_MISSING", "原始参考图不存在，请重新上传", 409)
    references = [fetch_bytes(reference_url, 20_000_000), fetch_bytes(state["views"][view]["url"], 20_000_000)]
    references.extend(
        fetch_bytes(state["views"][other]["url"], 20_000_000)
        for other in AI_GENERATED_VIEWS if other != view and other in state["views"]
    )
    generated, usage = generate_view(
        references, view_prompt(state["user_prompt"], view, data.prompt),
        models["image_model"], models["image_quality"],
    )
    revision = int(state["views"][view].get("revision", 0)) + 1
    url = PublicBlobStore().put(
        f"image3d/{session_id}/views/{view}-r{revision}.png", generated, "image/png",
    )
    state["views"][view] = {
        "url": url,
        "sha256": hashlib.sha256(generated).hexdigest(),
        "revision": revision,
        "prompt_revision": state["prompt_revision"],
        "generated": True,
        "refinement_prompt": data.prompt.strip(),
        "usage": usage,
    }
    state["views_stale"] = not _views_current(state)
    state["last_error"] = None
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/confirm")
def confirm_views(session_id: str, x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    if state["status"] != "VIEWS_REVIEW" or not _views_current(state):
        raise WorkflowError("VIEWS_NOT_READY", "请生成并检查当前提示词版本的正、左、右、背四视图", 409)
    state["views_stale"] = False
    state["confirmed_snapshot"] = {
        "prompt_revision": state["prompt_revision"],
        "user_prompt": state["user_prompt"],
        "views": {view: dict(state["views"][view]) for view in AI_GENERATED_VIEWS},
        "confirmed_at": int(time.time()),
    }
    state["status"] = "CONFIRMED"
    state["last_error"] = None
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/generate")
def generate_3d(session_id: str, data: Generate3DRequest,
                x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    models = _session_model_settings(state)
    if state["status"] not in {"CONFIRMED", "FAILED"} or not state.get("confirmed_snapshot"):
        raise WorkflowError("NOT_CONFIRMED", "请先确认正、左、右、背四视图", 409)
    if data.generate_type not in GENERATE_TYPES:
        raise WorkflowError("INVALID_OPTION", "生成类型只能是 Normal 或 Geometry", 422)
    options = data.model_dump()
    state["status"] = "SUBMITTING_3D"
    state["hunyuan_task"] = None
    state["last_error"] = None
    version = _save(state, version)
    snapshot = state["confirmed_snapshot"]
    if snapshot["views"]["front"]["url"].startswith("/local-blobs/"):
        state["status"] = "CONFIRMED"
        state["last_error"] = {
            "code": "PUBLIC_BLOB_REQUIRED",
            "message": "提交腾讯混元 3D 需要公网图片地址，请先配置 BLOB_READ_WRITE_TOKEN",
        }
        _save(state, version)
        raise WorkflowError(
            "PUBLIC_BLOB_REQUIRED",
            "本地图片无法被腾讯混元访问，请配置 BLOB_READ_WRITE_TOKEN 后重新创建会话",
            409,
        )
    try:
        created, hunyuan_model = submit_hunyuan(
            snapshot["views"]["front"]["url"],
            {view: snapshot["views"][view]["url"] for view in GENERATED_VIEWS},
            options,
            models["hunyuan_model"],
        )
    except WorkflowError as exc:
        state["status"] = "CONFIRMED"
        state["last_error"] = {"code": exc.code, "message": exc.message}
        _save(state, version)
        raise
    task_id = str(created.get("id", ""))
    if not TASK_ID.fullmatch(task_id):
        state["status"] = "CONFIRMED"
        state["last_error"] = {
            "code": "HUNYUAN_INVALID_RESPONSE",
            "message": "腾讯混元没有返回任务编号，请检查错误信息后重试",
        }
        _save(state, version)
        raise WorkflowError("HUNYUAN_INVALID_RESPONSE", "腾讯混元没有返回任务编号", 502)
    state["hunyuan_task"] = {
        "id": task_id,
        "model": hunyuan_model,
        "status": str(created.get("status", "queued")),
        "request_id": created.get("request_id"),
        "submitted_at": int(time.time()),
        "request": options,
        "models": {},
    }
    state["status"] = "GENERATING_3D"
    _save(state, version)
    return _response(state)


@app.get("/api/sessions/{session_id}/3d")
def poll_3d(session_id: str, x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    task = state.get("hunyuan_task") or {}
    if not TASK_ID.fullmatch(str(task.get("id", ""))):
        raise WorkflowError("TASK_NOT_CREATED", "3D任务尚未创建", 409)
    result = query_hunyuan(task["id"], task["model"])
    provider_status = str(result.get("status", "")).lower()
    status_map = {
        "queued": "GENERATING_3D", "pending": "GENERATING_3D",
        "in_progress": "GENERATING_3D", "processing": "GENERATING_3D", "running": "GENERATING_3D",
        "completed": "SUCCEEDED", "failed": "FAILED",
    }
    if provider_status not in status_map:
        state["status"] = "FAILED"
        state["last_error"] = {
            "code": "HUNYUAN_INVALID_RESPONSE",
            "message": f"腾讯混元返回未知任务状态：{provider_status or '空'}",
        }
        _save(state, version)
        raise WorkflowError("HUNYUAN_INVALID_RESPONSE", state["last_error"]["message"], 502)
    task["status"] = provider_status
    task["request_id"] = result.get("request_id", task.get("request_id"))
    task["completed_at"] = result.get("completed_at")
    task["credit_details"] = result.get("result_credit_details")
    if provider_status == "completed":
        blobs = PublicBlobStore()
        for item in result.get("data") or []:
            model_type = str(item.get("type", "")).lower()
            source_url = item.get("url")
            if model_type != "stl":
                continue
            if not model_type or not isinstance(source_url, str) or not source_url.startswith("https://"):
                continue
            if task["models"].get(model_type, {}).get("url"):
                continue
            raw = fetch_bytes(source_url)
            suffix = model_suffix(source_url, model_type)
            url = blobs.put(
                f"image3d/{session_id}/models/{model_type}{suffix}",
                raw,
                "application/octet-stream",
            )
            task["models"][model_type] = {
                "url": url,
                "preview_image_url": item.get("preview_image_url"),
                "size_bytes": len(raw),
            }
        if task["models"].get("stl", {}).get("url"):
            state["status"] = "SUCCEEDED"
            state["last_error"] = None
        else:
            state["status"] = "FAILED"
            state["last_error"] = {
                "code": "HUNYUAN_STL_MISSING",
                "message": "腾讯混元任务已完成，但没有返回可下载的 STL 文件，请重试或核对模型输出格式。",
            }
    elif provider_status == "failed":
        state["status"] = "FAILED"
        state["last_error"] = {
            "code": "HUNYUAN_GENERATION_FAILED",
            "message": str(result.get("error", result.get("message", "腾讯混元生成失败")))[:500],
        }
    else:
        state["status"] = "GENERATING_3D"
    state["hunyuan_task"] = task
    _save(state, version)
    return _response(state)


@app.get("/image-to-3d", include_in_schema=False)
def image_to_3d_page():
    return FileResponse(
        IMAGE3D_FRONTEND / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/local-blobs/{blob_path:path}", include_in_schema=False)
def local_blob(blob_path: str):
    path = local_blob_path(f"/local-blobs/{blob_path}")
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="本地文件不存在")
    return FileResponse(path)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(
        ORIGINAL_FRONTEND / "index.html",
        headers={"Cache-Control": "no-store"},
    )


app.mount("/assets", StaticFiles(directory=ORIGINAL_FRONTEND / "assets"), name="original-assets")
app.mount("/branding", StaticFiles(directory=ORIGINAL_FRONTEND / "branding"), name="original-branding")
app.mount("/static", StaticFiles(directory=IMAGE3D_FRONTEND), name="static")
