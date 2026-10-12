"""Standalone FastAPI entrypoint for Vercel."""
from __future__ import annotations

import hashlib
import os
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
    analyze_images,
    assembly_plan_prompt,
    effective_prompt,
    fetch_bytes,
    generate_image,
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
TEXT2IMAGE_FRONTEND = ROOT / "text2image_frontend"
TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
IMAGE_QUALITIES = {"low", "medium", "high", "xhigh", "max", "auto"}
VALID_STATES = {
    "REFERENCES_READY", "ANALYZING_REFERENCES", "PROMPT_REVIEW", "DESCRIPTION_CONFIRMED",
    "GENERATING_VIEWS", "VIEWS_REVIEW", "VIEWS_CONFIRMED", "CONFIRMED",
    "GENERATING_ASSEMBLY", "ASSEMBLY_REVIEW", "ASSEMBLY_CONFIRMED",
    "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED", "FAILED",
}
TEXT_IMAGE_STATES = {"QUEUED", "GENERATING", "PARTIAL", "SUCCEEDED"}
ANIMAL_CUTOUT_STATES = {"READY", "GENERATING", "SUCCEEDED", "FAILED"}
TRANSIENT_STATE_TIMEOUTS = {
    "ANALYZING_REFERENCES": 150,
    "GENERATING_VIEWS": 330,
    "GENERATING_ASSEMBLY": 330,
    "SUBMITTING_3D": 150,
}

MAX_REFERENCE_IMAGES = 6

app = FastAPI(title="图片生成 3D", version="1.0.0")


class PromptUpdate(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


class ViewGenerationRequest(BaseModel):
    views: list[str] = Field(default_factory=lambda: list(AI_GENERATED_VIEWS))


class ViewRefinementRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class AssemblyPlanRequest(BaseModel):
    prompt: str = Field(default="", max_length=4000)


class Generate3DRequest(BaseModel):
    generate_type: str = "Normal"
    enable_pbr: bool = False
    face_count: int | None = Field(default=None, ge=3000, le=1500000)
    result_format: Literal["STL"] = "STL"


class TextImageTaskRequest(BaseModel):
    prompts: list[str] = Field(min_length=1, max_length=5)
    model: str = Field(min_length=1, max_length=128)
    quality: str = Field(default="medium", max_length=16)
    size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1024x1024"


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
        if previous_status == "ANALYZING_REFERENCES":
            state["status"] = "REFERENCES_READY"
            state["last_error"] = {
                "code": "REFERENCE_ANALYSIS_TIMEOUT",
                "message": "参考图分析等待超时，已解除锁定，可重新分析。",
            }
        elif previous_status == "GENERATING_VIEWS":
            state["status"] = (
                "VIEWS_REVIEW" if state.get("views") else "DESCRIPTION_CONFIRMED"
            )
            state["last_error"] = {
                "code": "VIEW_GENERATION_TIMEOUT",
                "message": "多视图生成等待超时，已解除锁定；已完成的视图会保留，可重试未完成视图。",
            }
        elif previous_status == "GENERATING_ASSEMBLY":
            state["status"] = "ASSEMBLY_REVIEW" if state.get("assembly_plan") else "VIEWS_CONFIRMED"
            state["last_error"] = {
                "code": "ASSEMBLY_GENERATION_TIMEOUT",
                "message": "切片拼装图生成等待超时，已解除锁定，可重试。",
            }
        else:
            state["status"] = "ASSEMBLY_CONFIRMED"
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
        "reference_images": state.get("reference_images") or (
            [state["reference_image"]] if state.get("reference_image") else []
        ),
        "user_prompt": state.get("user_prompt", ""),
        "effective_prompt": state.get("effective_prompt", ""),
        "prompt_revision": state.get("prompt_revision", 1),
        "views_stale": state.get("views_stale", True),
        "views": state.get("views", {}),
        "confirmed_at": (state.get("confirmed_snapshot") or {}).get("confirmed_at"),
        "description_confirmed_at": state.get("description_confirmed_at"),
        "assembly_plan": state.get("assembly_plan"),
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


def _text_image_status(items: list[dict]) -> str:
    statuses = {item.get("status") for item in items}
    if statuses == {"SUCCEEDED"}:
        return "SUCCEEDED"
    if "GENERATING" in statuses:
        return "GENERATING"
    if "FAILED" in statuses:
        return "PARTIAL"
    return "QUEUED"


def _text_image_response(state: dict, *, token: str | None = None) -> dict:
    result = {
        "task_id": state["session_id"],
        "status": state["status"],
        "items": state["items"],
        "model_settings": state["model_settings"],
        "created_at": state["created_at"],
        "updated_at": state["updated_at"],
    }
    if token:
        result["access_token"] = token
    return result


def _load_text_image_task(task_id: str, token: str | None) -> tuple[dict, int]:
    state, version = load_session(task_id, _require_token(token))
    if state.get("workflow") != "text-to-image" or state.get("status") not in TEXT_IMAGE_STATES:
        raise SessionNotFound("文生图任务不存在")
    now = int(time.time())
    changed = False
    for item in state.get("items", []):
        started_at = int(item.get("started_at") or 0)
        if item.get("status") == "GENERATING" and started_at and now - started_at > 330:
            item["status"] = "FAILED"
            item["error"] = {
                "code": "IMAGE_GENERATION_TIMEOUT",
                "message": "本张图片生成等待超时，已解除锁定，可以单独重试。",
            }
            changed = True
    if changed:
        state["status"] = _text_image_status(state["items"])
        version = _save(state, version)
    return state, version


def _animal_cutout_response(state: dict, *, token: str | None = None) -> dict:
    result = {
        "task_id": state["session_id"],
        "status": state["status"],
        "prompt": state["prompt"],
        "source_url": state["source"]["url"],
        "result": state.get("result"),
        "last_error": state.get("last_error"),
        "model_settings": state["model_settings"],
        "created_at": state["created_at"],
        "updated_at": state["updated_at"],
    }
    if token:
        result["access_token"] = token
    return result


def _load_animal_cutout_task(task_id: str, token: str | None) -> tuple[dict, int]:
    state, version = load_session(task_id, _require_token(token))
    if state.get("workflow") != "animal-photo-cutout" or state.get("status") not in ANIMAL_CUTOUT_STATES:
        raise SessionNotFound("动物镂空图任务不存在")
    started_at = int(state.get("started_at") or 0)
    if state["status"] == "GENERATING" and started_at and int(time.time()) - started_at > 330:
        state["status"] = "FAILED"
        state["last_error"] = {
            "code": "ANIMAL_CUTOUT_TIMEOUT",
            "message": "图片生成等待超时，已解除锁定，可以重试。",
        }
        version = _save(state, version)
    return state, version


ANIMAL_CUTOUT_PROMPT = """
图片1是唯一主体参考。只保留图片中的主要动物，删除人物、手、牵引绳、家具、地面、风景、界面、文字和其他背景内容；若用户明确要求保留多个动物，则保持准确数量和相互姿态。不要把动物替换成通用品种或另一只动物，保留原动物的外轮廓、头身比例、姿态、耳形、口鼻、尾巴、主要毛色分区和1至3个最有辨识力的特征。
业务目标是把客户动物照片转成可供激光切割或CNC后续制图使用的单片平面产品设计，不是普通插画、素描或摄影滤镜。把动物转译为正视、居中的纯黑白平面镂空设计候选：纯白背景，纯黑表示保留材料，纯白表示去除区域。只能使用硬边纯黑与纯白，不使用灰度、彩色、阴影、渐变、照片纹理、立体材质、场景、边框、底座、文字、水印或尺寸线。
全部黑色保留材料必须形成一个连续整体。内部线条不能断开：每一条内部黑色线条、毛发块、眼鼻嘴细节和斑纹都必须至少有一个清晰可见且足够宽的连接点，连接到相邻黑线或主体黑色块；不得存在悬空黑色孤岛、断线、间隙、仅靠一个像素相连的细颈或封闭切割后会脱落的小块。用自然毛流和动物结构建立连接，不使用生硬横杆或大面积涂黑遮盖细节。
轮廓和内部负形要简洁、清楚、适合后续人工检查与矢量化。只输出一张完整设计，不输出原图对比、产品效果图、多方案拼贴或说明文字。本图只是视觉候选，不宣称已经通过实际尺寸、材料、刀宽、强度或加工验证。
""".strip()


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
    image_api_ready = bool(
        os.getenv("OPENAI_API_KEY", "").strip()
        and os.getenv("OPENAI_BASE_URL", "").strip()
    )
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
        "key_bound": image_api_ready,
        "api_setup_required": not image_api_ready,
        "free_services": {},
        "provider": "openai" if image_api_ready else "unconfigured",
        "mode": "SERVER_MANAGED" if image_api_ready else "CLOUD_COMPATIBILITY",
        "budget_scope": "LOCAL",
        "live_enabled": image_api_ready,
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


@app.get("/api/learning", include_in_schema=False)
def legacy_learning():
    return {
        "rules": [], "evidence": [], "snapshots": [],
        "active_snapshot_id": None, "pending": 0, "failures": [],
    }


@app.get("/api/schedules", include_in_schema=False)
@app.get("/api/recipes", include_in_schema=False)
def legacy_empty_settings_collections():
    return []


@app.get("/api/connections", include_in_schema=False)
def legacy_connections():
    return {"items": [], "inactive": [], "templates": [], "protocols": {}}


@app.get("/api/ai-connections", include_in_schema=False)
def legacy_ai_connections():
    return {"items": []}


@app.post("/api/text-image/tasks", status_code=201)
def create_text_image_task(data: TextImageTaskRequest):
    prompts = [prompt.strip() for prompt in data.prompts]
    if any(not prompt for prompt in prompts):
        raise WorkflowError("PROMPT_REQUIRED", "每一条图片要求都不能为空", 422)
    if any(len(prompt) > 2000 for prompt in prompts):
        raise WorkflowError("PROMPT_TOO_LONG", "每一条图片要求不能超过 2000 字", 422)
    if not MODEL_NAME.fullmatch(data.model.strip()):
        raise WorkflowError("INVALID_MODEL", "图片模型名称格式无效", 422)
    if data.quality not in IMAGE_QUALITIES:
        raise WorkflowError("INVALID_QUALITY", "图片质量设置无效", 422)

    task_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    state = {
        "session_id": task_id,
        "workflow": "text-to-image",
        "status": "QUEUED",
        "items": [
            {
                "index": index,
                "prompt": prompt,
                "status": "QUEUED",
                "url": None,
                "sha256": None,
                "size_bytes": None,
                "usage": {},
                "error": None,
                "started_at": None,
                "completed_at": None,
            }
            for index, prompt in enumerate(prompts)
        ],
        "model_settings": {
            "model": data.model.strip(),
            "quality": data.quality,
            "size": data.size,
        },
        "created_at": now,
        "updated_at": now,
    }
    create_session(task_id, token, state)
    return _text_image_response(state, token=token)


@app.get("/api/text-image/tasks/{task_id}")
def read_text_image_task(task_id: str, x_session_token: str | None = Header(default=None)):
    state, _ = _load_text_image_task(task_id, x_session_token)
    return _text_image_response(state)


@app.post("/api/text-image/tasks/{task_id}/items/{item_index}/generate")
def generate_text_image_item(task_id: str, item_index: int,
                             x_session_token: str | None = Header(default=None)):
    state, version = _load_text_image_task(task_id, x_session_token)
    if not 0 <= item_index < len(state["items"]):
        raise WorkflowError("IMAGE_ITEM_NOT_FOUND", "图片任务不存在", 404)
    item = state["items"][item_index]
    if item["status"] == "SUCCEEDED":
        return _text_image_response(state)
    if item["status"] == "GENERATING":
        raise WorkflowError("IMAGE_ALREADY_GENERATING", "这张图片正在生成，请勿重复提交", 409)

    item["status"] = "GENERATING"
    item["error"] = None
    item["started_at"] = int(time.time())
    state["status"] = "GENERATING"
    version = _save(state, version)
    try:
        settings = state["model_settings"]
        generated, usage = generate_image(
            item["prompt"], settings["model"], settings["quality"], settings["size"],
        )
        url = PublicBlobStore().put(
            f"text-image/{task_id}/{item_index + 1}.png",
            generated,
            "image/png",
        )
        state, version = _load_text_image_task(task_id, x_session_token)
        item = state["items"][item_index]
        item.update({
            "status": "SUCCEEDED",
            "url": url,
            "sha256": hashlib.sha256(generated).hexdigest(),
            "size_bytes": len(generated),
            "usage": usage,
            "error": None,
            "completed_at": int(time.time()),
        })
        state["status"] = _text_image_status(state["items"])
        _save(state, version)
        return _text_image_response(state)
    except Exception as exc:
        try:
            state, version = _load_text_image_task(task_id, x_session_token)
            item = state["items"][item_index]
            item["status"] = "FAILED"
            item["error"] = {
                "code": getattr(exc, "code", "IMAGE_GENERATION_FAILED"),
                "message": str(exc)[:500] or "图片生成失败",
            }
            state["status"] = _text_image_status(state["items"])
            _save(state, version)
        except StoreError:
            pass
        if isinstance(exc, (WorkflowError, StoreError)):
            raise
        raise WorkflowError("IMAGE_GENERATION_FAILED", "图片生成发生意外错误", 502) from exc


@app.post("/api/animal-cutout/tasks", status_code=201)
def create_animal_cutout_task(
    image: UploadFile = File(...),
    prompt: str = Form("", max_length=4000),
    image_model: str = Form(..., max_length=128),
    image_quality: str = Form("medium", max_length=16),
    size: Literal["1024x1024", "1536x1024", "1024x1536"] = Form("1024x1024"),
):
    model = image_model.strip()
    if not MODEL_NAME.fullmatch(model):
        raise WorkflowError("INVALID_MODEL", "图片模型名称格式无效", 422)
    if image_quality not in IMAGE_QUALITIES:
        raise WorkflowError("INVALID_QUALITY", "图片质量设置无效", 422)
    normalized, image_info = normalize_image(image.file.read(), image.content_type)
    task_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    source_url = PublicBlobStore().put(
        f"animal-cutout/{task_id}/source.png", normalized, "image/png",
    )
    now = int(time.time())
    state = {
        "session_id": task_id,
        "workflow": "animal-photo-cutout",
        "status": "READY",
        "prompt": prompt.strip() or ANIMAL_CUTOUT_PROMPT,
        "source": {
            "url": source_url,
            "sha256": hashlib.sha256(normalized).hexdigest(),
            "image_info": image_info,
        },
        "result": None,
        "last_error": None,
        "started_at": None,
        "model_settings": {
            "model": model,
            "quality": image_quality,
            "size": size,
        },
        "created_at": now,
        "updated_at": now,
    }
    create_session(task_id, token, state)
    return _animal_cutout_response(state, token=token)


@app.get("/api/animal-cutout/prompt-template")
def animal_cutout_prompt_template():
    return {"prompt": ANIMAL_CUTOUT_PROMPT}


@app.get("/api/animal-cutout/tasks/{task_id}")
def read_animal_cutout_task(task_id: str, x_session_token: str | None = Header(default=None)):
    state, _ = _load_animal_cutout_task(task_id, x_session_token)
    return _animal_cutout_response(state)


@app.post("/api/animal-cutout/tasks/{task_id}/generate")
def generate_animal_cutout_task(task_id: str, x_session_token: str | None = Header(default=None)):
    state, version = _load_animal_cutout_task(task_id, x_session_token)
    if state["status"] == "SUCCEEDED":
        return _animal_cutout_response(state)
    if state["status"] == "GENERATING":
        raise WorkflowError("ANIMAL_CUTOUT_ALREADY_GENERATING", "动物镂空图正在生成，请勿重复提交", 409)
    state["status"] = "GENERATING"
    state["started_at"] = int(time.time())
    state["last_error"] = None
    version = _save(state, version)
    try:
        source = fetch_bytes(state["source"]["url"], 20_000_000)
        settings = state["model_settings"]
        generated, usage = generate_view(
            [source],
            state["prompt"],
            settings["model"],
            settings["quality"],
            settings["size"],
        )
        result_url = PublicBlobStore().put(
            f"animal-cutout/{task_id}/result.png", generated, "image/png",
        )
        state, version = _load_animal_cutout_task(task_id, x_session_token)
        state["status"] = "SUCCEEDED"
        state["result"] = {
            "url": result_url,
            "sha256": hashlib.sha256(generated).hexdigest(),
            "size_bytes": len(generated),
            "usage": usage,
        }
        state["last_error"] = None
        state["completed_at"] = int(time.time())
        _save(state, version)
        return _animal_cutout_response(state)
    except Exception as exc:
        try:
            state, version = _load_animal_cutout_task(task_id, x_session_token)
            state["status"] = "FAILED"
            state["last_error"] = {
                "code": getattr(exc, "code", "ANIMAL_CUTOUT_FAILED"),
                "message": str(exc)[:500] or "动物镂空图生成失败",
            }
            _save(state, version)
        except StoreError:
            pass
        if isinstance(exc, (WorkflowError, StoreError)):
            raise
        raise WorkflowError("ANIMAL_CUTOUT_FAILED", "动物镂空图生成发生意外错误", 502) from exc


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
    user_prompt = prompt.strip()
    if view_source == "uploaded" and not user_prompt:
        user_prompt = "保持用户提交的各视图主体、结构、比例、材质和颜色一致"

    session_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    reference_url = PublicBlobStore().put(
        f"image3d/{session_id}/reference/01-primary.png", normalized, "image/png",
    )
    reference = {
        "url": reference_url,
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "role": "primary",
        "index": 1,
    }
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
        "status": "REFERENCES_READY" if view_source == "generated" else "VIEWS_REVIEW",
        "analysis": {},
        "input_notes": prompt.strip(),
        "user_prompt": user_prompt,
        "effective_prompt": effective_prompt(user_prompt) if user_prompt else "",
        "prompt_revision": 1,
        "views_stale": True,
        "views": initial_views,
        "reference_image": reference,
        "reference_images": [reference],
        "source_image": image_info,
        "description_confirmed_at": None,
        "confirmed_snapshot": None,
        "assembly_plan": None,
        "hunyuan_task": None,
        "last_error": None,
        "model_settings": models,
        "view_source": view_source,
        "created_at": now,
        "updated_at": now,
    }
    create_session(session_id, token, state)
    return _response(state, token=token)


@app.post("/api/sessions/{session_id}/references")
def upload_reference(session_id: str, image: UploadFile = File(...),
                     x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    if state.get("view_source") != "generated":
        raise WorkflowError("GENERATED_MODE_REQUIRED", "手动四视图模式不使用补充参考图", 409)
    if state["status"] != "REFERENCES_READY":
        raise WorkflowError("INVALID_SESSION_STATE", "只能在 AI 分析前添加补充参考图", 409)
    references = state.setdefault("reference_images", [])
    if len(references) >= MAX_REFERENCE_IMAGES:
        raise WorkflowError("TOO_MANY_REFERENCES", f"最多上传 {MAX_REFERENCE_IMAGES} 张参考图", 422)
    normalized, image_info = normalize_image(image.file.read(), image.content_type)
    index = len(references) + 1
    url = PublicBlobStore().put(
        f"image3d/{session_id}/reference/{index:02d}-supplemental.png",
        normalized,
        "image/png",
    )
    references.append({
        "url": url,
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "role": "supplemental",
        "index": index,
        "source_image": image_info,
    })
    state["last_error"] = None
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/analyze")
def analyze_references(session_id: str, x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    models = _session_model_settings(state)
    if state.get("view_source") != "generated" or state["status"] != "REFERENCES_READY":
        raise WorkflowError("INVALID_SESSION_STATE", "当前状态不能分析参考图", 409)
    references = state.get("reference_images") or []
    if not references:
        raise WorkflowError("REFERENCE_IMAGE_MISSING", "请至少上传一张参考图", 409)
    state["status"] = "ANALYZING_REFERENCES"
    state["last_error"] = None
    version = _save(state, version)
    try:
        images = [fetch_bytes(item["url"], 20_000_000) for item in references[:MAX_REFERENCE_IMAGES]]
        analysis = analyze_images(images, state.get("input_notes", ""), models["vision_model"])
    except Exception as exc:
        state["status"] = "REFERENCES_READY"
        state["last_error"] = {
            "code": getattr(exc, "code", "REFERENCE_ANALYSIS_FAILED"),
            "message": str(exc)[:500] or "参考图分析失败",
        }
        _save(state, version)
        if isinstance(exc, (WorkflowError, StoreError)):
            raise
        raise WorkflowError("REFERENCE_ANALYSIS_FAILED", "参考图分析发生意外错误", 502) from exc
    user_prompt = analysis.get("suggested_prompt_zh", "").strip()
    if not user_prompt:
        state["status"] = "REFERENCES_READY"
        state["last_error"] = {
            "code": "OPENAI_INVALID_RESPONSE",
            "message": "AI 没有返回可确认的图片描述",
        }
        _save(state, version)
        raise WorkflowError("OPENAI_INVALID_RESPONSE", state["last_error"]["message"], 502)
    state["analysis"] = analysis
    state["user_prompt"] = user_prompt
    state["effective_prompt"] = effective_prompt(user_prompt)
    state["description_confirmed_at"] = None
    state["status"] = "PROMPT_REVIEW"
    state["last_error"] = None
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/views/{view}/upload")
def upload_view(session_id: str, view: str, image: UploadFile = File(...),
                x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    if state.get("view_source") != "uploaded":
        raise WorkflowError("UPLOAD_MODE_REQUIRED", "当前会话使用 AI 生成四视图", 409)
    if state["status"] not in {
        "VIEWS_REVIEW", "VIEWS_CONFIRMED", "CONFIRMED", "ASSEMBLY_REVIEW",
        "ASSEMBLY_CONFIRMED", "FAILED",
    }:
        raise WorkflowError("INVALID_SESSION_STATE", "当前状态不能上传四视图", 409)
    if view not in AI_GENERATED_VIEWS:
        raise WorkflowError("INVALID_VIEW", "可上传视角为 front、left、right、back", 422)

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
    state["assembly_plan"] = None
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
    if state.get("view_source") != "generated":
        raise WorkflowError("GENERATED_MODE_REQUIRED", "手动上传四视图无需修改 AI 描述", 409)
    if state["status"] in {"REFERENCES_READY", "ANALYZING_REFERENCES", "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED"}:
        raise WorkflowError("SESSION_LOCKED", "3D任务提交后不能修改提示词", 409)
    prompt = data.prompt.strip()
    if prompt == state.get("user_prompt", ""):
        return _response(state)
    state["user_prompt"] = prompt
    state["effective_prompt"] = effective_prompt(state["user_prompt"])
    state["prompt_revision"] += 1
    state["views_stale"] = True
    state["description_confirmed_at"] = None
    state["confirmed_snapshot"] = None
    state["assembly_plan"] = None
    state["hunyuan_task"] = None
    state["last_error"] = None
    state["status"] = "PROMPT_REVIEW"
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/description/confirm")
def confirm_description(session_id: str, x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    if state.get("view_source") != "generated" or state["status"] != "PROMPT_REVIEW":
        raise WorkflowError("INVALID_SESSION_STATE", "请先上传参考图并完成 AI 分析", 409)
    if not state.get("user_prompt", "").strip():
        raise WorkflowError("EMPTY_DESCRIPTION", "AI 图片描述不能为空", 422)
    state["description_confirmed_at"] = int(time.time())
    state["status"] = "DESCRIPTION_CONFIRMED"
    state["last_error"] = None
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/views")
def create_views(session_id: str, data: ViewGenerationRequest,
                 x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    models = _session_model_settings(state)
    if state.get("view_source") != "generated":
        raise WorkflowError("GENERATED_MODE_REQUIRED", "当前会话使用手动上传视图", 409)
    if state["status"] not in {"DESCRIPTION_CONFIRMED", "VIEWS_REVIEW"}:
        raise WorkflowError("INVALID_SESSION_STATE", "当前状态不能生成多视图", 409)
    if not state.get("description_confirmed_at"):
        raise WorkflowError("DESCRIPTION_NOT_CONFIRMED", "请先确认 AI 对图片的描述", 409)
    requested = list(dict.fromkeys(data.views))
    if not requested or any(view not in AI_GENERATED_VIEWS for view in requested):
        raise WorkflowError("INVALID_VIEW", "可生成视角为 front、left、right、back", 422)
    state["status"] = "GENERATING_VIEWS"
    state["confirmed_snapshot"] = None
    state["assembly_plan"] = None
    state["hunyuan_task"] = None
    state["last_error"] = None
    version = _save(state, version)
    generated_views = {}
    failures: list[tuple[str, Exception]] = []
    try:
        references = state.get("reference_images") or []
        if not references and state.get("reference_image"):
            references = [state["reference_image"]]
        if not references:
            raise WorkflowError("REFERENCE_IMAGE_MISSING", "原始参考图不存在，请重新上传", 409)
        sources = [fetch_bytes(item["url"], 20_000_000) for item in references[:MAX_REFERENCE_IMAGES]]
        with ThreadPoolExecutor(max_workers=min(4, len(requested))) as executor:
            futures = {
                executor.submit(
                    generate_view,
                    sources,
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
    uploaded_references = state.get("reference_images") or []
    if not uploaded_references and state.get("reference_image"):
        uploaded_references = [state["reference_image"]]
    if not uploaded_references:
        raise WorkflowError("REFERENCE_IMAGE_MISSING", "原始参考图不存在，请重新上传", 409)
    references = [fetch_bytes(item["url"], 20_000_000) for item in uploaded_references[:MAX_REFERENCE_IMAGES]]
    references.append(fetch_bytes(state["views"][view]["url"], 20_000_000))
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
    state["confirmed_snapshot"] = None
    state["assembly_plan"] = None
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
    state["status"] = "VIEWS_CONFIRMED"
    state["assembly_plan"] = None
    state["last_error"] = None
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/assembly-plan")
def create_assembly_plan(session_id: str, data: AssemblyPlanRequest,
                         x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    models = _session_model_settings(state)
    if state["status"] not in {"VIEWS_CONFIRMED", "CONFIRMED", "ASSEMBLY_REVIEW"}:
        raise WorkflowError("VIEWS_NOT_CONFIRMED", "请先确认正、左、右、背四视图", 409)
    snapshot = state.get("confirmed_snapshot") or {}
    if not all(view in snapshot.get("views", {}) for view in AI_GENERATED_VIEWS):
        raise WorkflowError("VIEWS_NOT_CONFIRMED", "已确认的四视图不完整", 409)
    previous_status = state["status"]
    state["status"] = "GENERATING_ASSEMBLY"
    state["last_error"] = None
    version = _save(state, version)
    try:
        references = [
            fetch_bytes(snapshot["views"][view]["url"], 20_000_000)
            for view in AI_GENERATED_VIEWS
        ]
        generated, usage = generate_view(
            references,
            assembly_plan_prompt(snapshot.get("user_prompt") or state["user_prompt"], data.prompt),
            models["image_model"],
            models["image_quality"],
            "1536x1024",
        )
        previous = state.get("assembly_plan") or {}
        revision = int(previous.get("revision", 0)) + 1
        url = PublicBlobStore().put(
            f"image3d/{session_id}/assembly/plan-r{revision}.png",
            generated,
            "image/png",
        )
        state["assembly_plan"] = {
            "url": url,
            "sha256": hashlib.sha256(generated).hexdigest(),
            "revision": revision,
            "prompt": data.prompt.strip(),
            "usage": usage,
            "confirmed_at": None,
        }
        state["status"] = "ASSEMBLY_REVIEW"
        state["hunyuan_task"] = None
        state["last_error"] = None
        _save(state, version)
        return _response(state)
    except Exception as exc:
        state["status"] = "ASSEMBLY_REVIEW" if state.get("assembly_plan") else (
            "VIEWS_CONFIRMED" if previous_status != "CONFIRMED" else "CONFIRMED"
        )
        state["last_error"] = {
            "code": getattr(exc, "code", "ASSEMBLY_GENERATION_FAILED"),
            "message": str(exc)[:500] or "切片拼装图生成失败",
        }
        _save(state, version)
        if isinstance(exc, (WorkflowError, StoreError)):
            raise
        raise WorkflowError("ASSEMBLY_GENERATION_FAILED", "切片拼装图生成发生意外错误", 502) from exc


@app.post("/api/sessions/{session_id}/assembly-plan/confirm")
def confirm_assembly_plan(session_id: str, x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    plan = state.get("assembly_plan") or {}
    if state["status"] != "ASSEMBLY_REVIEW" or not plan.get("url"):
        raise WorkflowError("ASSEMBLY_NOT_READY", "请先生成并检查切片拼装图", 409)
    plan["confirmed_at"] = int(time.time())
    state["status"] = "ASSEMBLY_CONFIRMED"
    state["last_error"] = None
    _save(state, version)
    return _response(state)


@app.post("/api/sessions/{session_id}/generate")
def generate_3d(session_id: str, data: Generate3DRequest,
                x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    models = _session_model_settings(state)
    if (state["status"] not in {"ASSEMBLY_CONFIRMED", "FAILED"}
            or not state.get("confirmed_snapshot")
            or not (state.get("assembly_plan") or {}).get("confirmed_at")):
        raise WorkflowError("NOT_CONFIRMED", "请先确认四视图和切片拼装图", 409)
    if data.generate_type not in GENERATE_TYPES:
        raise WorkflowError("INVALID_OPTION", "生成类型只能是 Normal 或 Geometry", 422)
    options = data.model_dump()
    state["status"] = "SUBMITTING_3D"
    state["hunyuan_task"] = None
    state["last_error"] = None
    version = _save(state, version)
    snapshot = state["confirmed_snapshot"]
    if snapshot["views"]["front"]["url"].startswith("/local-blobs/"):
        state["status"] = "ASSEMBLY_CONFIRMED"
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
        state["status"] = "ASSEMBLY_CONFIRMED"
        state["last_error"] = {"code": exc.code, "message": exc.message}
        _save(state, version)
        raise
    task_id = str(created.get("id", ""))
    if not TASK_ID.fullmatch(task_id):
        state["status"] = "ASSEMBLY_CONFIRMED"
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


@app.get("/text-to-image", include_in_schema=False)
def text_to_image_page():
    return FileResponse(
        TEXT2IMAGE_FRONTEND / "index.html",
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
app.mount("/text-image-static", StaticFiles(directory=TEXT2IMAGE_FRONTEND), name="text-image-static")
