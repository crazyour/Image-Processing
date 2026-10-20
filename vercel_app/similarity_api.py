"""HTTP API for uploading and searching image, DXF, and Illustrator material folders."""
from __future__ import annotations

import base64
import hashlib
import io
import re
import uuid
import zipfile
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .material_blob import PrivateMaterialBlobStore
from .similarity_engine import SimilarityFileError, cosine_similarity, file_kind, process_file
from .similarity_store import (
    create_folder,
    create_library,
    get_asset,
    get_folder,
    list_folders,
    require_library,
    save_asset,
    searchable_assets,
    set_folder_result,
)
from .store import StoreError


router = APIRouter(prefix="/api/material-search", tags=["material-search"])
MAX_FILE_BYTES = 4_000_000
MAX_ARCHIVE_BYTES = 24_000_000
SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _authorise(library_id: str | None, token: str | None) -> str:
    if not library_id or not token:
        raise HTTPException(status_code=401, detail="缺少素材库访问令牌")
    try:
        require_library(library_id, token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return library_id


def _clean_name(value: str, fallback: str = "file") -> str:
    cleaned = SAFE_NAME.sub("-", PurePosixPath(value).name).strip(".-")
    return (cleaned or fallback)[:180]


def _clean_relative_path(value: str, filename: str) -> str:
    path = PurePosixPath(value or filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=400, detail="文件相对路径无效")
    cleaned = [_clean_name(part, "folder") for part in path.parts]
    cleaned[-1] = _clean_name(filename)
    return "/".join(cleaned)


def _content_type(kind: str, filename: str) -> str:
    if kind == "image":
        suffix = filename.lower().rsplit(".", 1)[-1]
        return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(suffix, "application/octet-stream")
    if kind == "dxf":
        return "application/dxf"
    return "application/postscript"


def _public_asset(asset: dict) -> dict:
    return {key: value for key, value in asset.items() if not key.startswith("_")}


def _public_folder(folder: dict) -> dict:
    public = {key: value for key, value in folder.items() if key != "assets"}
    public["assets"] = [_public_asset(asset) for asset in folder["assets"]]
    public["zip_url"] = f"/api/material-search/folders/{folder['id']}/download" if folder.get("zip_url") else None
    return public


def _stream_bytes(raw: bytes, chunk_size: int = 256 * 1024):
    for offset in range(0, len(raw), chunk_size):
        yield raw[offset:offset + chunk_size]


@router.post("/libraries")
def new_library():
    return create_library()


@router.post("/folders")
def new_folder(
    request: FolderCreateRequest,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    return create_folder(library_id, request.name.strip())


@router.get("/folders")
def folders(
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    return {"folders": [_public_folder(folder) for folder in list_folders(library_id)]}


@router.get("/folders/{folder_id}")
def folder_detail(
    folder_id: str,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    folder = get_folder(library_id, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="素材文件夹不存在")
    return _public_folder(folder)


@router.post("/folders/{folder_id}/assets")
async def upload_asset(
    folder_id: str,
    file: UploadFile = File(...),
    relative_path: str = Form(default=""),
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    folder = get_folder(library_id, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="素材文件夹不存在")
    filename = file.filename or "unnamed"
    try:
        kind = file_kind(filename)
    except SimilarityFileError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    raw = await file.read(MAX_FILE_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="第一版单个文件不能超过4 MB")
    try:
        processed_kind, preview, vector, metadata = process_file(filename, raw)
    except SimilarityFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if processed_kind != kind:
        raise HTTPException(status_code=422, detail="文件类型识别不一致")

    asset_key = uuid.uuid4().hex
    safe_filename = _clean_name(filename)
    clean_relative = _clean_relative_path(relative_path, filename)
    prefix = f"material-search/{library_id}/{folder_id}"
    store = PrivateMaterialBlobStore()
    try:
        blob_ref = store.put(f"{prefix}/original/{asset_key}-{safe_filename}", raw, _content_type(kind, filename))
        preview_ref = store.put(f"{prefix}/preview/{asset_key}.png", preview, "image/png")
        asset = save_asset(
            folder_id,
            kind=kind,
            original_name=filename,
            relative_path=clean_relative,
            blob_url=blob_ref,
            preview_url=preview_ref,
            file_size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            vector=vector,
            metadata=metadata,
        )
    except StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _public_asset(asset)


@router.post("/folders/{folder_id}/finalize")
def finalize_folder(
    folder_id: str,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    folder = get_folder(library_id, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="素材文件夹不存在")
    kinds = {asset["kind"] for asset in folder["assets"]}
    missing = [kind for kind in ("image", "dxf", "illustrator") if kind not in kinds]
    if missing:
        labels = {"image": "图片", "dxf": "DXF", "illustrator": "Illustrator AI"}
        message = "缺少：" + "、".join(labels[kind] for kind in missing)
        set_folder_result(folder_id, status="FAILED", error=message)
        raise HTTPException(status_code=409, detail=message)

    store = PrivateMaterialBlobStore()
    try:
        archive_buffer = io.BytesIO()
        total = 0
        with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for asset in folder["assets"]:
                raw, _ = store.read(asset["_blob_ref"])
                total += len(raw)
                if total > MAX_ARCHIVE_BYTES:
                    raise StoreError("文件夹总大小超过第一版24 MB的打包限制")
                archive.writestr(asset["relative_path"], raw)
        archive_bytes = archive_buffer.getvalue()
        safe_folder_name = _clean_name(folder["name"], "materials")
        zip_ref = store.put(
            f"material-search/{library_id}/{folder_id}/download/{safe_folder_name}.zip",
            archive_bytes,
            "application/zip",
        )
        set_folder_result(folder_id, status="READY", zip_url=zip_ref)
    except (StoreError, OSError, zipfile.BadZipFile) as exc:
        set_folder_result(folder_id, status="FAILED", error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    completed = get_folder(library_id, folder_id)
    return _public_folder(completed) if completed else None


@router.get("/assets/{asset_id}/preview")
def asset_preview(
    asset_id: str,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    asset = get_asset(library_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    try:
        raw, _ = PrivateMaterialBlobStore().read(asset["_preview_ref"])
    except StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(raw, media_type="image/png", headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"})


@router.get("/assets/{asset_id}/download")
def asset_download(
    asset_id: str,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    asset = get_asset(library_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    try:
        raw, _ = PrivateMaterialBlobStore().read(asset["_blob_ref"])
    except StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    filename = quote(asset["original_name"])
    return StreamingResponse(
        _stream_bytes(raw),
        media_type=_content_type(asset["kind"], asset["original_name"]),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/folders/{folder_id}/download")
def folder_download(
    folder_id: str,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    folder = get_folder(library_id, folder_id)
    if not folder or not folder.get("zip_url"):
        raise HTTPException(status_code=404, detail="文件夹下载包尚未生成")
    try:
        raw, _ = PrivateMaterialBlobStore().read(folder["zip_url"])
    except StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    filename = quote(_clean_name(folder["name"], "materials") + ".zip")
    return StreamingResponse(
        _stream_bytes(raw),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/search")
async def search(
    file: UploadFile = File(...),
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    filename = file.filename or "query"
    raw = await file.read(MAX_FILE_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="查询文件为空")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="第一版查询文件不能超过4 MB")
    try:
        kind, preview, query_vector, metadata = process_file(filename, raw)
    except SimilarityFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    best_by_folder: dict[str, dict] = {}
    for candidate in searchable_assets(library_id, kind):
        score = cosine_similarity(query_vector, candidate["vector"])
        previous = best_by_folder.get(candidate["folder_id"])
        if previous is None or score > previous["raw_score"]:
            best_by_folder[candidate["folder_id"]] = {**candidate, "raw_score": score}

    ranked = sorted(best_by_folder.values(), key=lambda item: item["raw_score"], reverse=True)[:5]
    results = []
    for rank, item in enumerate(ranked, start=1):
        folder = get_folder(library_id, item["folder_id"])
        if not folder:
            continue
        results.append({
            "rank": rank,
            "folder_id": item["folder_id"],
            "folder_name": item["folder_name"],
            "score": round(item["raw_score"] * 100, 1),
            "matched_asset_id": item["asset_id"],
            "matched_file": item["original_name"],
            "matched_preview_url": f"/api/material-search/assets/{item['asset_id']}/preview",
            "previews": folder["previews"],
            "zip_url": f"/api/material-search/folders/{folder['id']}/download" if folder.get("zip_url") else None,
            "asset_count": len(folder["assets"]),
        })
    return {
        "kind": kind,
        "query_preview_data_url": "data:image/png;base64," + base64.b64encode(preview).decode("ascii"),
        "query_metadata": metadata,
        "results": results,
    }
