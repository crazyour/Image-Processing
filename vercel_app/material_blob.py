"""Private object storage for user-owned material source files."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .store import StoreError, _local_data_root


LOCAL_PREFIX = "material-local://"


class PrivateMaterialBlobStore:
    """Use a dedicated private Vercel Blob store, with a private local fallback."""

    def __init__(self) -> None:
        self.token = os.getenv("MATERIAL_BLOB_READ_WRITE_TOKEN", "").strip()
        self.local = not bool(self.token)
        self.misconfigured_cloud = bool(os.getenv("VERCEL")) and self.local

    def _local_path(self, pathname: str) -> Path:
        relative = PurePosixPath(pathname)
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise StoreError("私有素材路径无效")
        root = (_local_data_root() / "material-private").resolve()
        destination = root.joinpath(*relative.parts).resolve()
        if destination == root or root not in destination.parents:
            raise StoreError("私有素材路径越界")
        return destination

    def put(self, pathname: str, data: bytes, content_type: str) -> str:
        if self.misconfigured_cloud:
            raise StoreError("Vercel环境缺少MATERIAL_BLOB_READ_WRITE_TOKEN，已拒绝写入临时磁盘")
        if self.local:
            destination = self._local_path(pathname)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            except OSError as exc:
                raise StoreError(f"私有素材保存失败：{exc}") from exc
            return LOCAL_PREFIX + pathname
        try:
            from vercel.blob import BlobClient

            blob = BlobClient().put(
                pathname,
                data,
                access="private",
                content_type=content_type,
                add_random_suffix=False,
                token=self.token,
            )
            return str(blob.pathname)
        except Exception as exc:
            raise StoreError("Vercel私有Blob保存失败；请确认素材专用Blob是Private模式") from exc

    def read(self, reference: str) -> tuple[bytes, str]:
        if self.misconfigured_cloud:
            raise StoreError("Vercel环境缺少MATERIAL_BLOB_READ_WRITE_TOKEN")
        if reference.startswith(LOCAL_PREFIX):
            pathname = reference[len(LOCAL_PREFIX):]
            try:
                return self._local_path(pathname).read_bytes(), "application/octet-stream"
            except OSError as exc:
                raise StoreError("私有素材读取失败") from exc
        if self.local:
            raise StoreError("当前未配置私有素材Blob，无法读取云端素材")
        try:
            from vercel.blob import BlobClient

            result = BlobClient().get(reference, access="private", token=self.token)
            if result is None or result.status_code != 200:
                raise StoreError("私有素材不存在")
            if hasattr(result, "content"):
                data = bytes(result)
                content_type = result.content_type or "application/octet-stream"
            else:
                stream = getattr(result, "stream", None)
                if stream is None:
                    raise StoreError("私有素材响应没有文件内容")
                data = b"".join(stream)
                blob = getattr(result, "blob", result)
                content_type = getattr(blob, "content_type", None) or "application/octet-stream"
            return data, content_type
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError("Vercel私有Blob读取失败") from exc

    def delete(self, references: list[str]) -> None:
        references = list(dict.fromkeys(reference for reference in references if reference))
        if not references:
            return
        if self.misconfigured_cloud:
            raise StoreError("Vercel环境缺少MATERIAL_BLOB_READ_WRITE_TOKEN")
        if self.local:
            try:
                for reference in references:
                    if reference.startswith(LOCAL_PREFIX):
                        self._local_path(reference[len(LOCAL_PREFIX):]).unlink(missing_ok=True)
            except OSError as exc:
                raise StoreError("私有素材删除失败") from exc
            return
        try:
            from vercel.blob import BlobClient

            client = BlobClient()
            for offset in range(0, len(references), 1000):
                client.delete(references[offset:offset + 1000], token=self.token)
        except Exception as exc:
            raise StoreError("Vercel私有Blob删除失败") from exc
