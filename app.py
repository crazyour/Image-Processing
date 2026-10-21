"""Standalone FastAPI entrypoint for Vercel."""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import re
import secrets
import time
import uuid
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
import cv2
import numpy as np
from PIL import Image
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vercel_app.store import (
    PublicBlobStore,
    SessionConflict,
    SessionNotFound,
    StoreError,
    create_session,
    list_local_sessions,
    load_local_preferences,
    local_blob_path,
    local_library_available,
    load_session,
    rotate_local_session_token,
    save_local_preferences,
    save_session,
    storage_status,
    update_local_session_library,
)
from vercel_app.connectivity import (
    ConnectivityError,
    analyze_connectivity,
    create_repair_mask,
    remove_disconnected_islands,
)
from vercel_app.similarity_api import router as similarity_search_router
from vercel_app.workflow import (
    AI_GENERATED_VIEWS,
    GENERATED_VIEWS,
    GENERATE_TYPES,
    VIEW_NAMES,
    WorkflowError,
    analyze_images,
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
SIMILARITY_SEARCH_FRONTEND = ROOT / "similarity_search_frontend"
WORKBENCH_TOOLS = {
    "creative", "photo-craft", "products", "color-finish", "visual",
    "svg", "dxf", "files", "delivery",
}
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
ANIMAL_CUTOUT_STATES = {
    "READY", "GENERATING", "CANDIDATES_READY", "REPAIR_REQUIRED", "REVIEW_REQUIRED", "SUCCEEDED", "FAILED",
}
TRANSIENT_STATE_TIMEOUTS = {
    "ANALYZING_REFERENCES": 150,
    "GENERATING_VIEWS": 330,
    "SUBMITTING_3D": 150,
}

MAX_REFERENCE_IMAGES = 6
MAX_REFERENCE_UPLOAD_BYTES = 4_000_000
MAX_AUTOMATIC_CUTOUT_REPAIRS = 2
CUTOUT_GENERATION_MODEL = "gpt-image-2.5-flare"
CUTOUT_GENERATION_QUALITY = "medium"
CUTOUT_REPAIR_MODEL = "gpt-image-2.5-sunburst"
CUTOUT_REPAIR_QUALITY = "high"
CUTOUT_IMAGE_SIZE = "1024x1024"
TEXT_IMAGE_MODEL = "gpt-image-2.5-flare"
TEXT_IMAGE_QUALITY = "medium"
TEXT_IMAGE_SIZE = "1024x1024"
IMAGE3D_VISION_MODEL = "gpt-6-luna"
IMAGE3D_VIEW_MODEL = "gpt-image-2.5-flare"
IMAGE3D_VIEW_QUALITY = "medium"
IMAGE3D_MODEL = "hy-3d-3.1"
MAX_DELIVERABLE_UPLOAD_BYTES = 4_000_000
DELIVERABLE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp",
    ".svg", ".ai", ".psd", ".pdf", ".dxf", ".dwg", ".stl", ".obj", ".glb",
    ".gltf", ".fbx", ".step", ".stp", ".iges", ".igs", ".3mf", ".zip",
    ".7z", ".rar", ".txt", ".csv", ".json", ".doc", ".docx", ".xls", ".xlsx",
}
DELIVERABLE_TASKS = {
    "text-image": "文生图",
    "animal-cutout": "照片转镂空剪影",
    "image-3d": "图生 3D",
    "design-engineering": "设计与工程文件",
    "other": "其他成果",
}

app = FastAPI(title="图片生成 3D", version="1.0.0")
app.include_router(similarity_search_router)


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


class TextImageTaskRequest(BaseModel):
    prompts: list[str] = Field(min_length=1, max_length=5)
    use_preferences: bool = True


class LibraryTaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    favorite: bool | None = None
    archived: bool | None = None


class LocalPreferencesUpdate(BaseModel):
    enabled: bool = False
    prompt: str = Field(default="", max_length=4000)


class AnimalCutoutRepairRequest(BaseModel):
    automatic: bool = False


class CutoutCandidateSelectRequest(BaseModel):
    candidate_index: int = Field(ge=0, le=4)


def _require_local_library_access(header: str | None, request: Request) -> None:
    client_host = request.client.host if request.client else ""
    if (
        header != "1"
        or not local_library_available()
        or client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}
    ):
        raise HTTPException(status_code=404, detail="本地作品库不可用")


def _active_preference_prompt(use_preferences: bool = True) -> str:
    if not use_preferences:
        return ""
    preferences = load_local_preferences()
    if not preferences.get("enabled"):
        return ""
    return str(preferences.get("prompt") or "").strip()


def _apply_personal_preferences(prompt: str, preference_prompt: str) -> str:
    prompt = prompt.strip()
    preference_prompt = preference_prompt.strip()
    if not preference_prompt:
        return prompt
    if not prompt:
        return f"个人长期偏好（本次已启用）：\n{preference_prompt}"
    return f"{prompt}\n\n个人长期偏好（本次已启用）：\n{preference_prompt}"


def _first_url(*values) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            url = value.get("url") or value.get("preview_url")
            if isinstance(url, str) and url:
                return url
        if isinstance(value, list):
            found = _first_url(*value)
            if found:
                return found
    return None


def _asset_urls(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found.update(_asset_urls(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_asset_urls(item))
    elif isinstance(value, str) and (
        value.startswith("/local-blobs/") or value.startswith("https://")
    ):
        found.add(value)
    return found


def _library_summary(record: dict) -> dict:
    state = record["state"]
    workflow = state.get("workflow") or "image-to-3d"
    library = state.get("library") or {}
    if workflow == "text-to-image":
        items = state.get("items") or []
        prompt = next((str(item.get("prompt") or "") for item in items if item.get("prompt")), "")
        preview_url = _first_url(
            [item for item in items if item.get("status") == "SUCCEEDED"],
            items,
            state.get("reference_images"),
        )
        default_title = prompt[:60] or "文生图任务"
    elif workflow == "animal-photo-cutout":
        prompt = str(state.get("prompt") or "")
        preview_url = _first_url(
            state.get("result"), state.get("candidates"), state.get("source"),
        )
        default_title = "照片转镂空剪影"
    else:
        prompt = str(state.get("input_notes") or state.get("user_prompt") or "")
        preview_url = _first_url(
            state.get("assembly_plan"),
            (state.get("views") or {}).get("front"),
            state.get("reference_images"),
            state.get("reference_image"),
        )
        default_title = prompt[:60] or "图生 3D 任务"
    return {
        "id": record["id"],
        "workflow": workflow,
        "status": state.get("status", "UNKNOWN"),
        "title": str(library.get("title") or default_title)[:120],
        "favorite": bool(library.get("favorite")),
        "archived": bool(library.get("archived")),
        "prompt": prompt[:500],
        "preview_url": preview_url,
        "asset_count": len(_asset_urls(state)),
        "created_at": state.get("created_at") or record.get("created_at"),
        "updated_at": state.get("updated_at") or record.get("updated_at"),
        "preference_applied": bool(state.get("preference_prompt")),
    }


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
    if state["status"] in {"GENERATING_ASSEMBLY", "ASSEMBLY_REVIEW", "ASSEMBLY_CONFIRMED"}:
        state["status"] = "VIEWS_CONFIRMED"
        state["last_error"] = None
        version = _save(state, version)
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
        else:
            state["status"] = "VIEWS_CONFIRMED"
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
        "view_source": state.get("view_source", "generated"),
        "preference_prompt": state.get("preference_prompt", ""),
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


def _text_image_reference_groups(state: dict) -> tuple[list[dict], list[dict]]:
    if "product_reference_images" in state:
        return state.get("product_reference_images") or [], state.get("example_reference_images") or []
    legacy = state.get("reference_images") or (
        [state["reference_image"]] if state.get("reference_image") else []
    )
    return legacy, []


def _text_image_edit_prompt(prompt: str, product_count: int, example_count: int) -> str:
    groups = []
    if product_count:
        groups.append(
            f"前 {product_count} 张是产品图片：以这些图片确定需要生成的产品主体、结构、材质、颜色和细节。"
        )
    if example_count:
        start = product_count + 1
        end = product_count + example_count
        groups.append(
            f"第 {start} 至 {end} 张是生成样例图片：只参考产品的摆放位置、构图、视角、比例、"
            "场景和整体视觉效果；不要把样例中的其他产品当成目标产品，也不要改变产品图片中的主体身份。"
        )
    return "\n".join([
        "输入图片已按用途分组，请严格区分两组图片。",
        *groups,
        f"用户生成要求：\n{prompt}",
    ])


def _text_image_response(state: dict, *, token: str | None = None) -> dict:
    product_references, example_references = _text_image_reference_groups(state)
    references = product_references + example_references
    result = {
        "task_id": state["session_id"],
        "status": state["status"],
        "items": state["items"],
        "reference_image": references[0] if references else None,
        "reference_images": references,
        "product_reference_images": product_references,
        "example_reference_images": example_references,
        "created_at": state["created_at"],
        "updated_at": state["updated_at"],
        "preference_prompt": state.get("preference_prompt", ""),
    }
    if token:
        result["access_token"] = token
    return result


def _image_svg(image: bytes, size: str) -> bytes:
    """Wrap a generated PNG losslessly in an SVG document without claiming vector paths."""
    try:
        width_text, height_text = size.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (AttributeError, TypeError, ValueError):
        width = height = 1024
    encoded = base64.b64encode(image).decode("ascii")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        '  <title>AI generated image (embedded PNG)</title>\n'
        f'  <image width="{width}" height="{height}" href="data:image/png;base64,{encoded}"/>\n'
        '</svg>\n'
    ).encode("utf-8")


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
    connectivity = state.get("connectivity")
    current_result = state.get("result")
    original_result = state.get("original_result")
    repairs = [
        {key: value for key, value in item.items() if key not in {"model", "quality"}}
        for item in state.get("repair_history", [])
    ]
    response = {
        "task_id": state["session_id"],
        "status": state["status"],
        "prompt": state["prompt"],
        "source_url": state["source"]["url"],
        "generation_count": state.get("generation_count", 1),
        "candidates": state.get("candidates", []),
        "selected_candidate_index": state.get("selected_candidate_index"),
        "result": current_result,
        "connectivity": connectivity,
        "repair_attempts": state.get("repair_attempts", 0),
        "automatic_repair_attempts": state.get("automatic_repair_attempts", 0),
        "max_automatic_repair_attempts": MAX_AUTOMATIC_CUTOUT_REPAIRS,
        "operation": state.get("operation"),
        "can_manual_repair": bool(
            current_result and connectivity and not connectivity.get("passed")
            and connectivity.get("island_count", 0) > 0
            and state.get("status") not in {"GENERATING", "FAILED"}
        ),
        "can_restore_original": bool(
            original_result and current_result
            and original_result.get("url") != current_result.get("url")
            and state.get("status") != "GENERATING"
        ),
        "process_details": {
            "generation_prompt": state.get("prompt", ""),
            "repair_prompt_template": ANIMAL_CUTOUT_REPAIR_PROMPT,
            "original_result": original_result,
            "repairs": repairs,
        },
        "last_error": state.get("last_error"),
        "created_at": state["created_at"],
        "updated_at": state["updated_at"],
        "preference_prompt": state.get("preference_prompt", ""),
    }
    if token:
        response["access_token"] = token
    return response


def _load_animal_cutout_task(task_id: str, token: str | None) -> tuple[dict, int]:
    state, version = load_session(task_id, _require_token(token))
    if state.get("workflow") != "animal-photo-cutout" or state.get("status") not in ANIMAL_CUTOUT_STATES:
        raise SessionNotFound("照片转镂空剪影任务不存在")
    started_at = int(state.get("started_at") or 0)
    if state["status"] == "GENERATING" and started_at and int(time.time()) - started_at > 330:
        operation = state.get("operation")
        repairing = operation == "repair" and state.get("result")
        selecting = operation == "select" and state.get("candidates")
        state["status"] = (
            "REVIEW_REQUIRED" if repairing else
            "CANDIDATES_READY" if selecting else
            "FAILED"
        )
        state["last_error"] = ({
            "code": "ANIMAL_REPAIR_TIMEOUT",
            "message": "AI 修复等待超时，已保留当前结果，可手动再次修复。",
        } if repairing else {
            "code": "CUTOUT_SELECTION_TIMEOUT",
            "message": "候选图连通性检测超时，已保留候选图，可重新选择。",
        } if selecting else {
            "code": "ANIMAL_CUTOUT_TIMEOUT",
            "message": "镂空剪影候选生成等待超时，已解除锁定，可以重试。",
        })
        state["operation"] = None
        version = _save(state, version)
    return state, version


ANIMAL_CUTOUT_PROMPT = """
核心目标
把照片转换为可用于激光切割 / CNC 制图的单片平面黑白镂空剪影设计。

主体要求
- 识别并保留照片中的主要视觉主体；主体可以是人物、动物、植物、器物、车辆、建筑、风景或它们的组合。
- 删除用户未要求保留的背景杂物、界面、文字和水印；若主体本身是风景或场景，则保留最有辨识力的地形、建筑、植物和空间层次。
- 保持原照片中主体的准确数量、外轮廓、比例、姿态、朝向、相互位置，以及 1–5 个最有辨识力的结构或纹理分区。
- 不得把主体替换成另一种人物、动物、物体、建筑或场景。

画面要求
- 构图清楚、主体突出，尽量保持原照片的视角和构图关系。
- 纯白背景。
- 纯黑表示保留材料，纯白表示去除区域。
- 只能使用硬边纯黑和纯白。
- 不使用灰度、彩色、阴影、渐变、照片纹理、立体效果、装饰性边框、文字、水印或尺寸线。

结构硬约束（最高优先级）
- 所有黑色保留材料必须整体连通，形成一个连续稳定的单片结构。
- 不得出现任何悬空黑色孤岛。
- 所有内部黑色线条、纹理块、五官、零件、枝叶、建筑或风景层次，必须通过清晰且足够宽的连接点连接到主体。
- 不得出现：
  - 断开的黑线
  - 被白色完全包围的小黑块
  - 极细细颈或单像素连接
  - 轮廓、纹理、五官、枝叶、零件或景物中的漂浮黑块
  - 切割后会脱落的细小局部细节
- 若某处细节无法自然连回主体，则宁可简化、合并或删除，也不要保留为独立黑块。

风格要求
- 连接方式尽量顺着主体的自然轮廓、结构走势、纹理方向或景物层次。
- 不要使用生硬横杆、机械式桥接，也不要大面积涂黑粗暴吞掉细节。
- 整体轮廓和内部负形简洁清楚，适合后续人工检查与矢量化。

输出要求
- 只输出一张完整设计图。
- 不输出原图对比、产品效果图、多方案拼贴或说明文字。
- 本图仅为视觉候选，不代表已通过实际加工验证。
""".strip()


ANIMAL_CUTOUT_REPAIR_PROMPT = """
图片1是必须保留的当前纯黑白镂空图，透明蒙版是唯一允许修改的局部。请在每个透明区域内，让断开的黑色部分跨过白色断缝，并顺着当前主体的轮廓、结构、纹理或景物层次自然连接到附近主体；无法自然连接就删除该孤岛，不能只重新描绘原图。
输出一张硬边纯黑白镂空图；蒙版外保持图片1不变，只修改蒙版内的连接，最终不得含灰度、颜色、标记或文字。
""".strip()


def _inspect_and_store_cutout(task_id: str, image: bytes, stem: str) -> dict:
    try:
        report, diagnostic = analyze_connectivity(image)
    except ConnectivityError as exc:
        raise WorkflowError("CONNECTIVITY_CHECK_FAILED", str(exc), 422) from exc
    report["diagnostic_url"] = PublicBlobStore().put(
        f"animal-cutout/{task_id}/{stem}-diagnostic.png", diagnostic, "image/png",
    )
    return report


def _cutout_repair_prompt(connectivity: dict) -> str:
    disconnected = int(connectivity.get("disconnected_island_count", 0))
    fragile = int(connectivity.get("fragile_connection_count", 0))
    issue_summary = f"本图有 {disconnected} 个断开孤岛"
    if fragile:
        issue_summary += f"和 {fragile} 处薄弱连接"
    return f"{issue_summary}。{ANIMAL_CUTOUT_REPAIR_PROMPT}"


def _cutout_result(image: bytes, url: str, usage: dict, connectivity: dict,
                   *, revision: int) -> dict:
    return {
        "url": url,
        "sha256": hashlib.sha256(image).hexdigest(),
        "size_bytes": len(image),
        "usage": usage,
        "revision": revision,
        "connectivity": connectivity,
    }


def _cutout_status(connectivity: dict, automatic_attempts: int) -> str:
    if connectivity.get("passed"):
        return "SUCCEEDED"
    if (
        connectivity.get("island_count", 0) > 0
        and automatic_attempts < MAX_AUTOMATIC_CUTOUT_REPAIRS
    ):
        return "REPAIR_REQUIRED"
    return "REVIEW_REQUIRED"


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
        "model_configuration": "server-managed",
        "storage": storage_status(),
    }


@app.get("/api/local-library/tasks")
def local_library_tasks(
    request: Request,
    x_local_library: str | None = Header(default=None),
):
    _require_local_library_access(x_local_library, request)
    tasks = [_library_summary(record) for record in list_local_sessions()]
    tasks.sort(key=lambda item: (not item["favorite"], -(int(item["updated_at"]) if isinstance(item["updated_at"], (int, float)) else 0)))
    return {"items": [item for item in tasks if not item["archived"]]}


@app.post("/api/local-library/tasks/{task_id}/resume")
def resume_local_library_task(
    task_id: str,
    request: Request,
    x_local_library: str | None = Header(default=None),
):
    _require_local_library_access(x_local_library, request)
    if not TASK_ID.fullmatch(task_id):
        raise HTTPException(status_code=404, detail="作品记录不存在")
    token = secrets.token_urlsafe(32)
    state = rotate_local_session_token(task_id, token)
    workflow = state.get("workflow") or "image-to-3d"
    return {
        "task_id": task_id,
        "access_token": token,
        "workflow": workflow,
        "page": "/text-to-image" if workflow in {"text-to-image", "animal-photo-cutout"} else "/image-to-3d",
    }


@app.patch("/api/local-library/tasks/{task_id}")
def update_local_library_task(
    task_id: str,
    data: LibraryTaskUpdate,
    request: Request,
    x_local_library: str | None = Header(default=None),
):
    _require_local_library_access(x_local_library, request)
    if not TASK_ID.fullmatch(task_id):
        raise HTTPException(status_code=404, detail="作品记录不存在")
    values = data.model_dump(exclude_none=True)
    if "title" in values:
        values["title"] = values["title"].strip()
    state = update_local_session_library(task_id, values)
    return _library_summary({
        "id": task_id,
        "state": state,
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
    })


@app.get("/api/local-library/preferences")
def get_local_library_preferences(
    request: Request,
    x_local_library: str | None = Header(default=None),
):
    _require_local_library_access(x_local_library, request)
    return load_local_preferences()


@app.put("/api/local-library/preferences")
def update_local_library_preferences(
    data: LocalPreferencesUpdate,
    request: Request,
    x_local_library: str | None = Header(default=None),
):
    _require_local_library_access(x_local_library, request)
    prompt = data.prompt.strip()
    if data.enabled and not prompt:
        raise WorkflowError("PREFERENCE_REQUIRED", "启用个人偏好前请先填写偏好内容", 422)
    return save_local_preferences(data.enabled, prompt)


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
        "key_bound": True,
        "api_setup_required": False,
        "free_services": {},
        "provider": "server-managed",
        "mode": "SERVER_MANAGED",
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
    preference_prompt = _active_preference_prompt(data.use_preferences)
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
            "model": TEXT_IMAGE_MODEL,
            "quality": TEXT_IMAGE_QUALITY,
            "size": TEXT_IMAGE_SIZE,
        },
        "preference_prompt": preference_prompt,
        "reference_image": None,
        "reference_images": [],
        "product_reference_images": [],
        "example_reference_images": [],
        "created_at": now,
        "updated_at": now,
    }
    create_session(task_id, token, state)
    return _text_image_response(state, token=token)


@app.post("/api/text-image/tasks/{task_id}/reference")
async def upload_text_image_reference(task_id: str, image: UploadFile = File(...),
                                      x_session_token: str | None = Header(default=None)):
    return await _upload_text_image_references(task_id, [image], [], x_session_token)


@app.post("/api/text-image/tasks/{task_id}/references")
async def upload_text_image_references(task_id: str,
                                       images: list[UploadFile] | None = File(default=None),
                                       product_images: list[UploadFile] | None = File(default=None),
                                       example_images: list[UploadFile] | None = File(default=None),
                                       x_session_token: str | None = Header(default=None)):
    return await _upload_text_image_references(
        task_id, product_images or images or [], example_images or [], x_session_token,
    )


async def _upload_text_image_references(task_id: str, product_images: list[UploadFile],
                                        example_images: list[UploadFile],
                                        x_session_token: str | None) -> dict:
    state, version = _load_text_image_task(task_id, x_session_token)
    if state["status"] != "QUEUED" or any(item["status"] != "QUEUED" for item in state["items"]):
        raise WorkflowError("REFERENCE_LOCKED", "图片开始生成后不能更换参考图", 409)
    if not product_images and not example_images:
        raise WorkflowError("REFERENCE_IMAGE_MISSING", "请至少上传一张参考图", 422)
    product_references, example_references = _text_image_reference_groups(state)
    if len(product_references) + len(product_images) > MAX_REFERENCE_IMAGES:
        raise WorkflowError("TOO_MANY_PRODUCT_REFERENCES", f"产品图片最多上传 {MAX_REFERENCE_IMAGES} 张", 422)
    if len(example_references) + len(example_images) > MAX_REFERENCE_IMAGES:
        raise WorkflowError("TOO_MANY_EXAMPLE_REFERENCES", f"生成样例图片最多上传 {MAX_REFERENCE_IMAGES} 张", 422)

    normalized_images = []
    for kind, images in (("product", product_images), ("example", example_images)):
        for image in images:
            raw = await image.read(MAX_REFERENCE_UPLOAD_BYTES + 1)
            normalized, metadata = normalize_image(raw, image.content_type)
            normalized_images.append((kind, image, normalized, metadata))

    blobs = PublicBlobStore()
    for kind, image, normalized, metadata in normalized_images:
        references = product_references if kind == "product" else example_references
        index = len(references) + 1
        url = blobs.put(
            f"text-image/{task_id}/references/{kind}/{index:02d}.png",
            normalized,
            "image/png",
        )
        references.append({
            "url": url,
            "filename": (image.filename or f"reference-{index}.png")[:255],
            **metadata,
        })
    references = product_references + example_references
    state["product_reference_images"] = product_references
    state["example_reference_images"] = example_references
    state["reference_images"] = references
    state["reference_image"] = references[0] if references else None
    _save(state, version)
    return _text_image_response(state)


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
        generation_prompt = _apply_personal_preferences(
            item["prompt"], state.get("preference_prompt", ""),
        )
        product_references, example_references = _text_image_reference_groups(state)
        references = product_references + example_references
        if references:
            reference_bytes = [
                fetch_bytes(reference["url"], 20_000_000)
                for reference in references[:MAX_REFERENCE_IMAGES]
            ]
            generated, usage = generate_view(
                reference_bytes,
                _text_image_edit_prompt(generation_prompt, len(product_references), len(example_references)),
                settings["model"], settings["quality"], settings["size"],
            )
        else:
            generated, usage = generate_image(
                generation_prompt, settings["model"], settings["quality"], settings["size"],
            )
        url = PublicBlobStore().put(
            f"text-image/{task_id}/{item_index + 1}.png",
            generated,
            "image/png",
        )
        svg = _image_svg(generated, settings["size"])
        svg_url = PublicBlobStore().put(
            f"text-image/{task_id}/{item_index + 1}.svg",
            svg,
            "image/svg+xml",
        )
        state, version = _load_text_image_task(task_id, x_session_token)
        item = state["items"][item_index]
        item.update({
            "status": "SUCCEEDED",
            "url": url,
            "svg_url": svg_url,
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
    generation_count: int = Form(3, ge=1, le=5),
    use_preferences: bool = Form(True),
):
    normalized, image_info = normalize_image(image.file.read(), image.content_type)
    preference_prompt = _active_preference_prompt(use_preferences)
    generation_prompt = _apply_personal_preferences(
        prompt.strip() or ANIMAL_CUTOUT_PROMPT, preference_prompt,
    )
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
        "prompt": generation_prompt,
        "preference_prompt": preference_prompt,
        "source": {
            "url": source_url,
            "sha256": hashlib.sha256(normalized).hexdigest(),
            "image_info": image_info,
        },
        "result": None,
        "original_result": None,
        "generation_count": generation_count,
        "candidates": [],
        "selected_candidate_index": None,
        "connectivity": None,
        "repair_attempts": 0,
        "automatic_repair_attempts": 0,
        "repair_history": [],
        "operation": None,
        "last_error": None,
        "started_at": None,
        "model_settings": {
            "model": CUTOUT_GENERATION_MODEL,
            "quality": CUTOUT_GENERATION_QUALITY,
            "repair_model": CUTOUT_REPAIR_MODEL,
            "repair_quality": CUTOUT_REPAIR_QUALITY,
            "size": CUTOUT_IMAGE_SIZE,
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


def _cutout_dxf(image_bytes: bytes) -> bytes:
    """Trace black raster regions into closed, millimetre-based DXF polylines."""
    with Image.open(io.BytesIO(image_bytes)) as source:
        gray = np.asarray(source.convert("L"))
    mask = np.where(gray < 128, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    height, width = mask.shape
    scale = 100.0 / max(width, height)
    entities = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        simplified = cv2.approxPolyDP(contour, max(0.8, perimeter * 0.0015), True)
        points = simplified.reshape(-1, 2)
        if len(points) < 3 or abs(cv2.contourArea(simplified)) < 4:
            continue
        vertices = "".join(
            f"10\n{x * scale:.4f}\n20\n{(height - y) * scale:.4f}\n"
            for x, y in points
        )
        entities.append(f"0\nLWPOLYLINE\n8\nCUT\n90\n{len(points)}\n70\n1\n{vertices}")
    if not entities:
        raise WorkflowError("CUTOUT_DXF_EMPTY", "当前结果没有可导出的黑色轮廓", 409)
    header = "0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n4\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n"
    return (header + "".join(entities) + "0\nENDSEC\n0\nEOF\n").encode("ascii")


@app.get("/api/animal-cutout/tasks/{task_id}/dxf")
def download_animal_cutout_dxf(
    task_id: str,
    x_session_token: str | None = Header(default=None),
):
    state, _ = _load_animal_cutout_task(task_id, x_session_token)
    result = state.get("result") or {}
    if state.get("status") not in {"SUCCEEDED", "REVIEW_REQUIRED"} or not result.get("url"):
        raise WorkflowError("ANIMAL_CUTOUT_NOT_READY", "还没有可导出的最终结果", 409)
    dxf = _cutout_dxf(fetch_bytes(result["url"], 25_000_000))
    return Response(
        dxf,
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="cutout-{task_id[:8]}.dxf"'},
    )


@app.post("/api/animal-cutout/tasks/{task_id}/generate")
def generate_animal_cutout_task(task_id: str, x_session_token: str | None = Header(default=None)):
    state, version = _load_animal_cutout_task(task_id, x_session_token)
    if state.get("candidates"):
        return _animal_cutout_response(state)
    if state["status"] == "GENERATING":
        raise WorkflowError("CUTOUT_ALREADY_GENERATING", "镂空剪影候选正在生成，请勿重复提交", 409)
    state["status"] = "GENERATING"
    state["operation"] = "generate"
    state["started_at"] = int(time.time())
    state["last_error"] = None
    version = _save(state, version)
    try:
        source = fetch_bytes(state["source"]["url"], 20_000_000)
        settings = state["model_settings"]
        generation_count = int(state.get("generation_count", 1))

        def generate_candidate(index: int) -> dict:
            generated, usage = generate_view(
                [source],
                state["prompt"],
                settings["model"],
                settings["quality"],
                settings["size"],
            )
            candidate_url = PublicBlobStore().put(
                f"animal-cutout/{task_id}/candidate-{index + 1}.png",
                generated,
                "image/png",
            )
            return {
                "index": index,
                "url": candidate_url,
                "sha256": hashlib.sha256(generated).hexdigest(),
                "size_bytes": len(generated),
                "usage": usage,
            }

        candidates: list[dict] = []
        failures: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(generation_count, 3)) as executor:
            futures = {
                executor.submit(generate_candidate, index): index
                for index in range(generation_count)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    candidates.append(future.result())
                except Exception as exc:
                    failures.append({
                        "index": index,
                        "code": getattr(exc, "code", "CUTOUT_CANDIDATE_FAILED"),
                        "message": str(exc)[:300] or "候选图生成失败",
                    })
        candidates.sort(key=lambda item: item["index"])
        if not candidates:
            first_error = failures[0] if failures else {"message": "候选图生成失败"}
            raise WorkflowError("CUTOUT_CANDIDATES_FAILED", first_error["message"], 502)

        state, version = _load_animal_cutout_task(task_id, x_session_token)
        state["status"] = "CANDIDATES_READY"
        state["candidates"] = candidates
        state["candidate_failures"] = failures
        state["selected_candidate_index"] = None
        state["result"] = None
        state["original_result"] = None
        state["connectivity"] = None
        state["repair_attempts"] = 0
        state["automatic_repair_attempts"] = 0
        state["repair_history"] = []
        state["operation"] = None
        state["last_error"] = ({
            "code": "CUTOUT_CANDIDATES_PARTIAL",
            "message": f"{len(failures)} 张候选生成失败，其余候选仍可选择。",
        } if failures else None)
        state["completed_at"] = int(time.time())
        _save(state, version)
        return _animal_cutout_response(state)
    except Exception as exc:
        try:
            state, version = _load_animal_cutout_task(task_id, x_session_token)
            state["status"] = "FAILED"
            state["operation"] = None
            state["last_error"] = {
                "code": getattr(exc, "code", "CUTOUT_GENERATION_FAILED"),
                "message": str(exc)[:500] or "镂空剪影候选生成失败",
            }
            _save(state, version)
        except StoreError:
            pass
        if isinstance(exc, (WorkflowError, StoreError)):
            raise
        raise WorkflowError("CUTOUT_GENERATION_FAILED", "镂空剪影候选生成发生意外错误", 502) from exc


@app.post("/api/animal-cutout/tasks/{task_id}/select")
def select_cutout_candidate(
    task_id: str,
    data: CutoutCandidateSelectRequest,
    x_session_token: str | None = Header(default=None),
):
    state, version = _load_animal_cutout_task(task_id, x_session_token)
    if state["status"] == "GENERATING":
        raise WorkflowError("CUTOUT_ALREADY_GENERATING", "当前任务正在处理，请稍后再选择", 409)
    candidate = next(
        (item for item in state.get("candidates", []) if int(item.get("index", -1)) == data.candidate_index),
        None,
    )
    if not candidate:
        raise WorkflowError("CUTOUT_CANDIDATE_NOT_FOUND", "所选候选图不存在", 404)

    state["status"] = "GENERATING"
    state["operation"] = "select"
    state["started_at"] = int(time.time())
    state["last_error"] = None
    version = _save(state, version)
    try:
        selected = fetch_bytes(candidate["url"], 25_000_000)
        connectivity = _inspect_and_store_cutout(
            task_id,
            selected,
            f"candidate-{data.candidate_index + 1}-selected",
        )
        result = _cutout_result(
            selected,
            candidate["url"],
            candidate.get("usage", {}),
            connectivity,
            revision=0,
        )
        state, version = _load_animal_cutout_task(task_id, x_session_token)
        state["selected_candidate_index"] = data.candidate_index
        state["result"] = result
        state["original_result"] = result
        state["connectivity"] = connectivity
        state["repair_attempts"] = 0
        state["automatic_repair_attempts"] = 0
        state["repair_history"] = []
        state["status"] = _cutout_status(connectivity, 0)
        state["operation"] = None
        state["last_error"] = None
        state["completed_at"] = int(time.time())
        _save(state, version)
        return _animal_cutout_response(state)
    except Exception as exc:
        try:
            state, version = _load_animal_cutout_task(task_id, x_session_token)
            state["status"] = "CANDIDATES_READY"
            state["operation"] = None
            state["last_error"] = {
                "code": getattr(exc, "code", "CUTOUT_SELECTION_FAILED"),
                "message": str(exc)[:500] or "候选图检测失败",
            }
            _save(state, version)
        except StoreError:
            pass
        if isinstance(exc, (WorkflowError, StoreError)):
            raise
        raise WorkflowError("CUTOUT_SELECTION_FAILED", "候选图检测发生意外错误", 502) from exc


@app.post("/api/animal-cutout/tasks/{task_id}/repair")
def repair_animal_cutout_task(
    task_id: str,
    data: AnimalCutoutRepairRequest,
    x_session_token: str | None = Header(default=None),
):
    state, version = _load_animal_cutout_task(task_id, x_session_token)
    if state["status"] == "GENERATING":
        raise WorkflowError("ANIMAL_CUTOUT_ALREADY_GENERATING", "镂空剪影正在处理，请勿重复提交", 409)
    result = state.get("result")
    connectivity = state.get("connectivity") or (result or {}).get("connectivity")
    if not result or not connectivity:
        raise WorkflowError("ANIMAL_CUTOUT_NOT_READY", "还没有可修复的黑白镂空图", 409)
    if connectivity.get("passed"):
        return _animal_cutout_response(state)
    if connectivity.get("island_count", 0) <= 0:
        raise WorkflowError("ANIMAL_CUTOUT_NOT_REPAIRABLE", "当前结果没有可自动修复的孤岛，请人工处理", 409)

    automatic_attempts = int(state.get("automatic_repair_attempts", 0))
    if data.automatic and automatic_attempts >= MAX_AUTOMATIC_CUTOUT_REPAIRS:
        state["status"] = "REVIEW_REQUIRED"
        version = _save(state, version)
        return _animal_cutout_response(state)

    attempt = int(state.get("repair_attempts", 0)) + 1
    if data.automatic:
        automatic_attempts += 1
    state["status"] = "GENERATING"
    state["operation"] = "repair"
    state["repair_attempts"] = attempt
    state["automatic_repair_attempts"] = automatic_attempts
    state["started_at"] = int(time.time())
    state["last_error"] = None
    version = _save(state, version)

    repair_prompt = ""
    try:
        current = fetch_bytes(result["url"], 25_000_000)
        # Always rebuild the guide so older tasks also use the current
        # blue-subject/red-issue diagnostic convention.
        repair_connectivity, _ = analyze_connectivity(current)
        repair_mask = create_repair_mask(current)
        mask_url = PublicBlobStore().put(
            f"animal-cutout/{task_id}/repair-{attempt}-mask.png",
            repair_mask,
            "image/png",
        )
        settings = state["model_settings"]
        repair_model = settings.get("repair_model", CUTOUT_REPAIR_MODEL)
        repair_quality = settings.get("repair_quality", CUTOUT_REPAIR_QUALITY)
        repair_prompt = _cutout_repair_prompt(repair_connectivity)
        repaired, usage = generate_view(
            [current],
            repair_prompt,
            repair_model,
            repair_quality,
            settings["size"],
            repair_mask,
        )
        ai_repaired_url = PublicBlobStore().put(
            f"animal-cutout/{task_id}/repair-{attempt}-ai.png", repaired, "image/png",
        )
        repaired_connectivity, _ = analyze_connectivity(repaired)
        local_cleanup_applied = False
        if repaired_connectivity.get("disconnected_island_count", 0) > 0:
            cleaned = remove_disconnected_islands(repaired)
            cleaned_connectivity, _ = analyze_connectivity(cleaned)
            if cleaned_connectivity.get("island_count", 0) < repaired_connectivity.get("island_count", 0):
                repaired = cleaned
                local_cleanup_applied = True
        repaired_url = PublicBlobStore().put(
            f"animal-cutout/{task_id}/repair-{attempt}.png", repaired, "image/png",
        )
        repaired_connectivity = _inspect_and_store_cutout(task_id, repaired, f"repair-{attempt}")
        repaired_result = _cutout_result(
            repaired, repaired_url, usage, repaired_connectivity, revision=attempt,
        )

        state, version = _load_animal_cutout_task(task_id, x_session_token)
        state["result"] = repaired_result
        state["connectivity"] = repaired_connectivity
        state.setdefault("repair_history", []).append({
            "attempt": attempt,
            "automatic": data.automatic,
            "status": "COMPLETED",
            "prompt": repair_prompt,
            "model": repair_model,
            "quality": repair_quality,
            "input_url": result["url"],
            "mask_url": mask_url,
            "ai_result_url": ai_repaired_url,
            "local_cleanup_applied": local_cleanup_applied,
            "url": repaired_url,
            "result": repaired_result,
            "connectivity": repaired_connectivity,
            "usage": usage,
            "completed_at": int(time.time()),
        })
        state["status"] = (
            _cutout_status(repaired_connectivity, automatic_attempts)
            if data.automatic else
            ("SUCCEEDED" if repaired_connectivity.get("passed") else "REVIEW_REQUIRED")
        )
        state["operation"] = None
        state["last_error"] = None
        state["completed_at"] = int(time.time())
        _save(state, version)
        return _animal_cutout_response(state)
    except Exception as exc:
        try:
            state, version = _load_animal_cutout_task(task_id, x_session_token)
            error = {
                "code": getattr(exc, "code", "ANIMAL_REPAIR_FAILED"),
                "message": (str(exc)[:500] or "AI 修复失败") + "；已保留修复前结果。",
            }
            state["status"] = "REVIEW_REQUIRED"
            state["operation"] = None
            state["last_error"] = error
            state.setdefault("repair_history", []).append({
                "attempt": attempt,
                "automatic": data.automatic,
                "status": "FAILED",
                "prompt": repair_prompt or ANIMAL_CUTOUT_REPAIR_PROMPT,
                "error": error,
                "completed_at": int(time.time()),
            })
            _save(state, version)
        except StoreError:
            pass
        if isinstance(exc, (WorkflowError, StoreError)):
            raise
        raise WorkflowError("ANIMAL_REPAIR_FAILED", "AI 修复发生意外错误", 502) from exc


@app.post("/api/animal-cutout/tasks/{task_id}/restore-original")
def restore_original_animal_cutout(
    task_id: str,
    x_session_token: str | None = Header(default=None),
):
    state, version = _load_animal_cutout_task(task_id, x_session_token)
    if state["status"] == "GENERATING":
        raise WorkflowError("ANIMAL_CUTOUT_ALREADY_GENERATING", "镂空剪影正在处理，请稍后再恢复", 409)
    original = state.get("original_result")
    if not original:
        raise WorkflowError("ORIGINAL_RESULT_NOT_FOUND", "原始生成图不可用", 404)
    connectivity = original.get("connectivity")
    state["result"] = original
    state["connectivity"] = connectivity
    state["status"] = "SUCCEEDED" if connectivity and connectivity.get("passed") else "REVIEW_REQUIRED"
    state["operation"] = None
    state["last_error"] = None
    state["completed_at"] = int(time.time())
    _save(state, version)
    return _animal_cutout_response(state)


@app.post("/api/sessions")
def new_session(
    image: UploadFile = File(...),
    prompt: str = Form("", max_length=4000),
    view_source: Literal["generated", "uploaded"] = Form("generated"),
    use_preferences: bool = Form(True),
):
    models = _model_settings(
        IMAGE3D_VISION_MODEL, IMAGE3D_VIEW_MODEL, IMAGE3D_VIEW_QUALITY, IMAGE3D_MODEL,
    )
    normalized, image_info = normalize_image(image.file.read(), image.content_type)
    preference_prompt = _active_preference_prompt(use_preferences)
    base_prompt = prompt.strip()
    if view_source == "uploaded" and not base_prompt:
        base_prompt = "保持用户提交的各视图主体、结构、比例、材质和颜色一致"
    input_notes = _apply_personal_preferences(base_prompt, preference_prompt)
    user_prompt = input_notes

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
        "input_notes": input_notes,
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
        "workflow": "image-to-3d",
        "preference_prompt": preference_prompt,
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
    user_prompt = _apply_personal_preferences(
        analysis.get("suggested_prompt_zh", "").strip(),
        state.get("preference_prompt", ""),
    )
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


@app.post("/api/sessions/{session_id}/generate")
def generate_3d(session_id: str, data: Generate3DRequest,
                x_session_token: str | None = Header(default=None)):
    state, version = _load(session_id, x_session_token)
    models = _session_model_settings(state)
    if state["status"] not in {"VIEWS_CONFIRMED", "CONFIRMED", "FAILED"} or not state.get("confirmed_snapshot"):
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
        state["status"] = "VIEWS_CONFIRMED"
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
        state["status"] = "VIEWS_CONFIRMED"
        state["last_error"] = {"code": exc.code, "message": exc.message}
        _save(state, version)
        raise
    task_id = str(created.get("id", ""))
    if not TASK_ID.fullmatch(task_id):
        state["status"] = "VIEWS_CONFIRMED"
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


@app.get("/similarity-search", include_in_schema=False)
def similarity_search_page():
    return FileResponse(
        SIMILARITY_SEARCH_FRONTEND / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/deliverables/upload", status_code=201)
async def upload_deliverable(
    file: UploadFile = File(...),
    task: str = Form("other"),
):
    """Store a generated deliverable and return a shareable download URL."""
    original_name = Path(file.filename or "").name.strip()
    extension = Path(original_name).suffix.lower()
    if not original_name or extension not in DELIVERABLE_EXTENSIONS:
        raise HTTPException(status_code=422, detail="不支持该文件类型")
    if task not in DELIVERABLE_TASKS:
        raise HTTPException(status_code=422, detail="请选择有效的所属任务")

    data = await file.read(MAX_DELIVERABLE_UPLOAD_BYTES + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=422, detail="文件内容为空")
    if len(data) > MAX_DELIVERABLE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="单个文件不能超过 4 MB")

    safe_stem = re.sub(r"[^\w\-.]+", "-", Path(original_name).stem, flags=re.UNICODE).strip("-._")
    safe_name = f"{safe_stem[:80] or 'file'}{extension}"
    content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    # Vector markup is downloadable as data rather than executable browser content.
    if extension in {".svg", ".ai"}:
        content_type = "application/octet-stream"
    upload_id = uuid.uuid4().hex
    try:
        url = PublicBlobStore().put(
            f"deliverables/{task}/{time.strftime('%Y/%m/%d')}/{upload_id}/{safe_name}",
            data,
            content_type,
        )
    except StoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "id": upload_id,
        "name": original_name,
        "size": len(data),
        "content_type": content_type,
        "url": url,
        "task": task,
        "task_label": DELIVERABLE_TASKS[task],
    }


@app.get("/api/deliverables")
def list_deliverables():
    try:
        stored = PublicBlobStore().list("deliverables/", limit=100)
    except StoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
    items = []
    for item in stored:
        path_parts = Path(item["pathname"]).parts
        task = path_parts[1] if len(path_parts) > 1 and path_parts[1] in DELIVERABLE_TASKS else "uncategorized"
        name = Path(item["pathname"]).name
        extension = Path(name).suffix.lower()
        uploaded_at = item["uploaded_at"]
        if hasattr(uploaded_at, "isoformat"):
            uploaded_at = uploaded_at.isoformat()
        items.append({
            "name": name, "url": item["url"], "size": item["size"],
            "type": "image" if extension in image_extensions else "file",
            "extension": extension.removeprefix(".").upper() or "FILE",
            "uploaded_at": uploaded_at,
            "task": task,
            "task_label": DELIVERABLE_TASKS.get(task, "未分类"),
        })
    items.sort(key=lambda item: str(item["uploaded_at"]), reverse=True)
    return {"items": items}


@app.get("/file-upload", include_in_schema=False)
def file_upload_page():
    return FileResponse(
        ORIGINAL_FRONTEND / "upload.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/tools/{tool_slug}", include_in_schema=False)
def standalone_tool_page(tool_slug: str):
    if tool_slug not in WORKBENCH_TOOLS:
        raise HTTPException(status_code=404, detail="功能不存在")
    return FileResponse(
        ORIGINAL_FRONTEND / "index.html",
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
        ORIGINAL_FRONTEND / "home.html",
        headers={"Cache-Control": "no-store"},
    )


app.mount("/assets", StaticFiles(directory=ORIGINAL_FRONTEND / "assets"), name="original-assets")
app.mount("/branding", StaticFiles(directory=ORIGINAL_FRONTEND / "branding"), name="original-branding")
app.mount("/static", StaticFiles(directory=IMAGE3D_FRONTEND), name="static")
app.mount("/text-image-static", StaticFiles(directory=TEXT2IMAGE_FRONTEND), name="text-image-static")
app.mount(
    "/similarity-search-static",
    StaticFiles(directory=SIMILARITY_SEARCH_FRONTEND),
    name="similarity-search-static",
)
