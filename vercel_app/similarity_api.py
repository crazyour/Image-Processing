"""HTTP API for uploading and searching image, DXF, and Illustrator material folders."""
from __future__ import annotations

import base64
import hashlib
import re
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .material_blob import PrivateMaterialBlobStore
from .similarity_engine import (
    FEATURE_VERSION,
    SimilarityFileError,
    cosine_similarity,
    file_kind,
    image_geometric_similarity,
    process_file,
)
from .similarity_store import (
    create_folder,
    create_library,
    expired_deleted_folders,
    get_asset,
    get_folder,
    list_deleted_folders,
    list_folders,
    move_folder_to_trash,
    permanently_delete_folder,
    require_library,
    restore_folder,
    save_asset,
    searchable_assets,
    set_folder_result,
    update_asset_index,
)
from .store import StoreError


router = APIRouter(prefix="/api/material-search", tags=["material-search"])
MAX_CHUNK_BYTES = 3_000_000
TRASH_RETENTION_SECONDS = 30 * 24 * 60 * 60
SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
UPLOAD_ID = re.compile(r"^[a-f0-9]{32}$")


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("文件夹名称为必填项")
        return name


class ChunkCompleteRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    relative_path: str = Field(default="", max_length=1000)
    chunk_count: int = Field(ge=1)


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


def _validate_upload_id(upload_id: str) -> str:
    if not UPLOAD_ID.fullmatch(upload_id):
        raise HTTPException(status_code=400, detail="上传编号无效")
    return upload_id


def _chunk_references(staging_prefix: str, upload_id: str, chunk_count: int) -> list[str]:
    return [f"{staging_prefix}/{upload_id}/{index:08d}.part" for index in range(chunk_count)]


def _read_chunks(store: PrivateMaterialBlobStore, references: list[str]) -> bytes:
    try:
        return b"".join(store.read(reference)[0] for reference in references)
    except StoreError as exc:
        raise HTTPException(status_code=502, detail="上传分片不完整，请重新上传") from exc


def _process_and_save_asset(
    library_id: str,
    folder_id: str,
    filename: str,
    relative_path: str,
    raw: bytes,
) -> dict:
    try:
        kind = file_kind(filename)
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
        return save_asset(
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


def _public_asset(asset: dict) -> dict:
    public = {key: value for key, value in asset.items() if not key.startswith("_")}
    metadata = dict(public.get("metadata") or {})
    metadata.pop("local_features", None)
    metadata.pop("image_fingerprint", None)
    public["metadata"] = metadata
    return public


def _public_folder(folder: dict) -> dict:
    public = {key: value for key, value in folder.items() if key != "assets"}
    public["assets"] = [_public_asset(asset) for asset in folder["assets"]]
    public["zip_url"] = f"/api/material-search/folders/{folder['id']}/download" if folder.get("zip_url") else None
    return public


def _stream_bytes(raw: bytes, chunk_size: int = 256 * 1024):
    for offset in range(0, len(raw), chunk_size):
        yield raw[offset:offset + chunk_size]


def _blob_references(folder: dict) -> list[str]:
    references = [folder.get("zip_url")]
    for asset in folder["assets"]:
        references.extend((asset["_blob_ref"], asset["_preview_ref"]))
    return [reference for reference in references if reference]


def _permanently_delete(library_id: str, folder: dict) -> None:
    try:
        PrivateMaterialBlobStore().delete(_blob_references(folder))
    except StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not permanently_delete_folder(library_id, folder["id"]):
        raise HTTPException(status_code=404, detail="回收站中的素材文件夹不存在")


def _purge_expired_trash(library_id: str) -> None:
    cutoff = time.time() - TRASH_RETENTION_SECONDS
    for folder in expired_deleted_folders(library_id, cutoff):
        try:
            PrivateMaterialBlobStore().delete(_blob_references(folder))
            permanently_delete_folder(library_id, folder["id"])
        except StoreError:
            continue


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
    return create_folder(library_id, request.name)


@router.get("/folders")
def folders(
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    _purge_expired_trash(library_id)
    return {"folders": [_public_folder(folder) for folder in list_folders(library_id)]}


@router.get("/trash")
def trash(
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    _purge_expired_trash(library_id)
    folders = []
    for folder in list_deleted_folders(library_id):
        public = _public_folder(folder)
        public["expires_at"] = folder["deleted_at"] + TRASH_RETENTION_SECONDS
        folders.append(public)
    return {"folders": folders, "retention_days": 30}


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


@router.delete("/folders/{folder_id}", status_code=204)
def remove_folder(
    folder_id: str,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    if not move_folder_to_trash(library_id, folder_id):
        raise HTTPException(status_code=404, detail="素材文件夹不存在")
    return Response(status_code=204)


@router.post("/trash/{folder_id}/restore")
def restore_trashed_folder(
    folder_id: str,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    _purge_expired_trash(library_id)
    if not restore_folder(library_id, folder_id):
        raise HTTPException(status_code=404, detail="回收站中的素材文件夹不存在")
    folder = get_folder(library_id, folder_id)
    return _public_folder(folder) if folder else None


@router.delete("/trash/{folder_id}", status_code=204)
def permanently_remove_folder(
    folder_id: str,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    folder = get_folder(library_id, folder_id, include_deleted=True)
    if not folder or folder["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="回收站中的素材文件夹不存在")
    _permanently_delete(library_id, folder)
    return Response(status_code=204)


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
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    return _public_asset(_process_and_save_asset(library_id, folder_id, filename, relative_path, raw))


@router.post("/folders/{folder_id}/uploads/{upload_id}/chunks/{chunk_index}")
async def upload_asset_chunk(
    folder_id: str,
    upload_id: str,
    chunk_index: int,
    chunk: UploadFile = File(...),
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    _validate_upload_id(upload_id)
    if chunk_index < 0:
        raise HTTPException(status_code=400, detail="上传分片序号无效")
    if not get_folder(library_id, folder_id):
        raise HTTPException(status_code=404, detail="素材文件夹不存在")
    raw = await chunk.read(MAX_CHUNK_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="上传分片为空")
    if len(raw) > MAX_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail="上传分片超过平台请求限制")
    reference = f"material-search/{library_id}/{folder_id}/staging/{upload_id}/{chunk_index:08d}.part"
    try:
        PrivateMaterialBlobStore().put(reference, raw, "application/octet-stream")
    except StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"chunk_index": chunk_index}


@router.post("/folders/{folder_id}/uploads/{upload_id}/complete")
def complete_asset_upload(
    folder_id: str,
    upload_id: str,
    request: ChunkCompleteRequest,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    _validate_upload_id(upload_id)
    if not get_folder(library_id, folder_id):
        raise HTTPException(status_code=404, detail="素材文件夹不存在")
    staging_prefix = f"material-search/{library_id}/{folder_id}/staging"
    references = _chunk_references(staging_prefix, upload_id, request.chunk_count)
    store = PrivateMaterialBlobStore()
    try:
        raw = _read_chunks(store, references)
        if not raw:
            raise HTTPException(status_code=400, detail="文件为空")
        asset = _process_and_save_asset(
            library_id, folder_id, request.filename, request.relative_path, raw,
        )
        return _public_asset(asset)
    finally:
        try:
            store.delete(references)
        except StoreError:
            pass


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
    missing = [kind for kind in ("image", "illustrator") if kind not in kinds]
    if missing:
        labels = {"image": "图片", "dxf": "DXF", "illustrator": "Illustrator AI"}
        message = "缺少：" + "、".join(labels[kind] for kind in missing)
        set_folder_result(folder_id, status="FAILED", error=message)
        raise HTTPException(status_code=409, detail=message)

    store = PrivateMaterialBlobStore()
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip") as archive_file:
            with zipfile.ZipFile(archive_file.name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for asset in folder["assets"]:
                    raw, _ = store.read(asset["_blob_ref"])
                    archive.writestr(asset["relative_path"], raw)
            safe_folder_name = _clean_name(folder["name"], "materials")
            zip_ref = store.put_file(
                f"material-search/{library_id}/{folder_id}/download/{safe_folder_name}.zip",
                Path(archive_file.name),
                "application/zip",
            )
        set_folder_result(folder_id, status="READY", zip_url=zip_ref)
    except (StoreError, OSError, zipfile.BadZipFile) as exc:
        set_folder_result(folder_id, status="FAILED", error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    completed = get_folder(library_id, folder_id)
    return _public_folder(completed) if completed else None


@router.post("/reindex")
def reindex_library_images(
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    store = PrivateMaterialBlobStore()
    updated = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    for folder in list_folders(library_id):
        for asset in folder["assets"]:
            if asset["kind"] != "image":
                continue
            metadata = asset.get("metadata") or {}
            features = metadata.get("local_features") or {}
            if metadata.get("feature_version") == FEATURE_VERSION and features.get("data"):
                skipped += 1
                continue
            try:
                raw, _ = store.read(asset["_blob_ref"])
                kind, _, vector, new_metadata = process_file(asset["original_name"], raw)
                if kind != "image" or not update_asset_index(
                    library_id,
                    asset["id"],
                    vector=vector,
                    metadata=new_metadata,
                ):
                    raise StoreError("图片索引记录不存在")
                updated += 1
            except (StoreError, SimilarityFileError, OSError) as exc:
                failures.append({"folder": folder["name"], "file": asset["original_name"], "error": str(exc)})
    return {
        "feature_version": FEATURE_VERSION,
        "updated": updated,
        "skipped": skipped,
        "failed": len(failures),
        "failures": failures[:20],
    }


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
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="查询文件为空")
    return _search_materials(library_id, filename, raw)


@router.post("/search/uploads/{upload_id}/chunks/{chunk_index}")
async def upload_search_chunk(
    upload_id: str,
    chunk_index: int,
    chunk: UploadFile = File(...),
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    _validate_upload_id(upload_id)
    if chunk_index < 0:
        raise HTTPException(status_code=400, detail="上传分片序号无效")
    raw = await chunk.read(MAX_CHUNK_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="上传分片为空")
    if len(raw) > MAX_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail="上传分片超过平台请求限制")
    reference = f"material-search/{library_id}/query-staging/{upload_id}/{chunk_index:08d}.part"
    try:
        PrivateMaterialBlobStore().put(reference, raw, "application/octet-stream")
    except StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"chunk_index": chunk_index}


@router.post("/search/uploads/{upload_id}/complete")
def complete_search_upload(
    upload_id: str,
    request: ChunkCompleteRequest,
    library_id: str | None = Header(default=None, alias="X-Material-Library"),
    token: str | None = Header(default=None, alias="X-Material-Token"),
):
    library_id = _authorise(library_id, token)
    _validate_upload_id(upload_id)
    staging_prefix = f"material-search/{library_id}/query-staging"
    references = _chunk_references(staging_prefix, upload_id, request.chunk_count)
    store = PrivateMaterialBlobStore()
    try:
        raw = _read_chunks(store, references)
        if not raw:
            raise HTTPException(status_code=400, detail="查询文件为空")
        return _search_materials(library_id, request.filename, raw)
    finally:
        try:
            store.delete(references)
        except StoreError:
            pass


def _search_materials(library_id: str, filename: str, raw: bytes) -> dict:
    try:
        kind, preview, query_vector, metadata = process_file(filename, raw)
    except SimilarityFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    matches: list[dict] = []
    outdated_images = 0
    for candidate in searchable_assets(library_id, kind):
        global_score = cosine_similarity(query_vector, candidate["vector"])
        if kind == "image":
            if (candidate.get("metadata") or {}).get("feature_version") != FEATURE_VERSION:
                outdated_images += 1
            verification = image_geometric_similarity(metadata, candidate.get("metadata") or {}, global_score)
        else:
            verification = {
                "score": global_score,
                "label": "特征相似",
                "inliers": 0,
                "inlier_ratio": 0.0,
                "coverage": 0.0,
                "method": "cosine",
            }
        matches.append({
            **candidate,
            "raw_score": verification["score"],
            "verification": verification,
        })

    # Rank files globally; one folder may contribute multiple top-five files.
    ranked = sorted(matches, key=lambda item: item["raw_score"], reverse=True)[:5]
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
            "match_label": item["verification"]["label"],
            "match_method": item["verification"]["method"],
            "inliers": item["verification"]["inliers"],
            "inlier_ratio": round(item["verification"]["inlier_ratio"], 3),
            "coverage": round(item["verification"]["coverage"], 3),
            "previews": folder["previews"],
            "zip_url": f"/api/material-search/folders/{folder['id']}/download" if folder.get("zip_url") else None,
            "asset_count": len(folder["assets"]),
        })
    return {
        "kind": kind,
        "query_preview_data_url": "data:image/png;base64," + base64.b64encode(preview).decode("ascii"),
        "query_metadata": {key: value for key, value in metadata.items() if key not in {"local_features", "image_fingerprint"}},
        "algorithm": "sift-ransac" if kind == "image" else "cosine",
        "outdated_images": outdated_images,
        "results": results,
    }
