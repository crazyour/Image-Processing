"""CPU-only interactive object segmentation using the official EdgeSAM ONNX graphs."""
from __future__ import annotations

import hashlib
import io
import os
import platform
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


MODEL_NAME = "EdgeSAM-3x ONNX"
MODEL_SIZE = 1024
MAX_IMAGE_BYTES = 12_000_000
MAX_IMAGE_PIXELS = 16_000_000
MAX_SESSIONS = 8
SESSION_TTL_SECONDS = 15 * 60

ENCODER_URL = "https://huggingface.co/spaces/chongzhou/EdgeSAM/resolve/main/weights/edge_sam_3x_encoder.onnx"
DECODER_URL = "https://huggingface.co/spaces/chongzhou/EdgeSAM/resolve/main/weights/edge_sam_3x_decoder.onnx"
ENCODER_SHA256 = "719a498cf5b3fe9be9f01ee513e13d3915f9028aa4f23dfd30eaaa0a17143159"
DECODER_SHA256 = "83a2174d54571596913dcb7455d021e713623c3dca30a31c8c41ab98c9fb0863"

PIXEL_MEAN = np.asarray([123.675, 116.28, 103.53], dtype=np.float32)[None, :, None, None]
PIXEL_STD = np.asarray([58.395, 57.12, 57.375], dtype=np.float32)[None, :, None, None]


class SegmentationError(RuntimeError):
    pass


class SegmentationUnavailable(SegmentationError):
    pass


class SegmentationSessionNotFound(SegmentationError):
    pass


@dataclass
class SegmentationSession:
    library_id: str
    features: np.ndarray
    original_size: tuple[int, int]
    input_size: tuple[int, int]
    created_at: float
    last_accessed_at: float


def _import_onnxruntime() -> Any:
    """Load ONNX Runtime, falling back to the bundled Linux inference core."""
    try:
        import onnxruntime as ort

        return ort
    except ImportError as first_error:
        machine = platform.machine().lower()
        if platform.system() != "Linux" or machine not in {"x86_64", "amd64"}:
            raise SegmentationUnavailable("智能点选需要安装 onnxruntime。") from first_error

        vendor_root = Path(__file__).resolve().parent / "_vendor"
        if not (vendor_root / "onnxruntime" / "__init__.py").is_file():
            raise SegmentationUnavailable("智能点选推理核心未随部署文件发布。") from first_error
        vendor_path = str(vendor_root)
        if vendor_path not in sys.path:
            sys.path.insert(0, vendor_path)
        try:
            import onnxruntime as ort

            return ort
        except (ImportError, OSError) as exc:
            raise SegmentationUnavailable(f"智能点选推理核心无法加载：{exc}") from exc


class EdgeSamOnnxRuntime:
    """Small ONNX wrapper matching EdgeSAM's official SamPredictorONNX pipeline."""

    def __init__(self, encoder_path: Path, decoder_path: Path) -> None:
        ort = _import_onnxruntime()

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.inter_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        try:
            self.encoder = ort.InferenceSession(str(encoder_path), sess_options=options, providers=providers)
            self.decoder = ort.InferenceSession(str(decoder_path), sess_options=options, providers=providers)
        except Exception as exc:
            raise SegmentationUnavailable(f"智能点选模型无法加载：{exc}") from exc

    def encode(self, image: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
        tensor, input_size = preprocess_image(image)
        try:
            features = self.encoder.run(None, {"image": tensor})[0]
        except Exception as exc:
            raise SegmentationError(f"主体分析失败：{exc}") from exc
        return np.asarray(features, dtype=np.float32), input_size

    def decode(
        self,
        session: SegmentationSession,
        points: list[dict[str, Any]],
    ) -> tuple[np.ndarray, float]:
        height, width = session.original_size
        input_height, input_width = session.input_size
        coords = np.asarray(
            [[[float(point["x"]) * input_width, float(point["y"]) * input_height] for point in points]],
            dtype=np.float32,
        )
        labels = np.asarray([[float(point["label"]) for point in points]], dtype=np.float32)
        try:
            outputs = self.decoder.run(None, {
                "image_embeddings": session.features,
                "point_coords": coords,
                "point_labels": labels,
            })
        except Exception as exc:
            raise SegmentationError(f"选区计算失败：{exc}") from exc

        scores = np.asarray(outputs[0]).reshape(-1)
        low_res_masks = np.asarray(outputs[1], dtype=np.float32)
        best_index = int(np.argmax(scores))
        mask = postprocess_mask(
            low_res_masks[0, best_index],
            session.input_size,
            session.original_size,
        )
        return mask, float(scores[best_index])


_runtime: EdgeSamOnnxRuntime | None = None
_runtime_lock = threading.Lock()
_sessions: OrderedDict[str, SegmentationSession] = OrderedDict()
_sessions_lock = threading.Lock()


def _model_root() -> Path:
    configured = os.getenv("EDGESAM_MODEL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_data = os.getenv("LOCAL_DATA_DIR", "").strip()
    if local_data:
        return Path(local_data).expanduser().resolve() / "models" / "edgesam"
    if os.getenv("VERCEL", "").strip():
        return Path("/tmp/lingjie-image3d/models/edgesam")
    return Path(__file__).resolve().parent.parent / "var" / "models" / "edgesam"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_model(path: Path, url: str, expected_sha256: str) -> None:
    if path.is_file() and _sha256(path) == expected_sha256:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Lingjie-EdgeSAM/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        temporary.unlink(missing_ok=True)
        raise SegmentationUnavailable(
            "智能点选模型下载失败，请检查网络后重试。"
        ) from exc
    if _sha256(temporary) != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise SegmentationUnavailable("智能点选模型校验失败，请重新下载。")
    temporary.replace(path)


def _model_paths() -> tuple[Path, Path]:
    encoder_override = os.getenv("EDGESAM_ENCODER_PATH", "").strip()
    decoder_override = os.getenv("EDGESAM_DECODER_PATH", "").strip()
    root = _model_root()
    encoder = Path(encoder_override).expanduser().resolve() if encoder_override else root / "edge_sam_3x_encoder.onnx"
    decoder = Path(decoder_override).expanduser().resolve() if decoder_override else root / "edge_sam_3x_decoder.onnx"
    if encoder_override and not encoder.is_file():
        raise SegmentationUnavailable("EDGESAM_ENCODER_PATH 指向的模型不存在。")
    if decoder_override and not decoder.is_file():
        raise SegmentationUnavailable("EDGESAM_DECODER_PATH 指向的模型不存在。")
    if not encoder_override:
        _download_model(encoder, ENCODER_URL, ENCODER_SHA256)
    if not decoder_override:
        _download_model(decoder, DECODER_URL, DECODER_SHA256)
    return encoder, decoder


def get_runtime() -> EdgeSamOnnxRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is None:
            encoder, decoder = _model_paths()
            _runtime = EdgeSamOnnxRuntime(encoder, decoder)
    return _runtime


def decode_image(raw: bytes) -> np.ndarray:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise SegmentationError("智能点选图片为空或超过 12 MB。")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                image = background.convert("RGB")
            else:
                image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise SegmentationError("智能点选图片无法读取。") from exc
    if image.width < 2 or image.height < 2:
        raise SegmentationError("智能点选图片尺寸无效。")
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise SegmentationError("智能点选图片像素过大，请缩小到 1600 万像素以内。")
    return np.asarray(image, dtype=np.uint8)


def preprocess_image(image: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    height, width = image.shape[:2]
    scale = MODEL_SIZE / max(height, width)
    input_height = max(1, int(height * scale + 0.5))
    input_width = max(1, int(width * scale + 0.5))
    resized = cv2.resize(image, (input_width, input_height), interpolation=cv2.INTER_LINEAR)
    tensor = resized.transpose(2, 0, 1)[None].astype(np.float32)
    tensor = (tensor - PIXEL_MEAN) / PIXEL_STD
    tensor = np.pad(
        tensor,
        ((0, 0), (0, 0), (0, MODEL_SIZE - input_height), (0, MODEL_SIZE - input_width)),
        mode="constant",
    ).astype(np.float32, copy=False)
    return tensor, (input_height, input_width)


def postprocess_mask(
    low_res_mask: np.ndarray,
    input_size: tuple[int, int],
    original_size: tuple[int, int],
) -> np.ndarray:
    mask = cv2.resize(low_res_mask.astype(np.float32), (MODEL_SIZE, MODEL_SIZE), interpolation=cv2.INTER_LINEAR)
    mask = mask[:input_size[0], :input_size[1]]
    mask = cv2.resize(mask, (original_size[1], original_size[0]), interpolation=cv2.INTER_LINEAR)
    return np.where(mask > 0.0, 255, 0).astype(np.uint8)


def _purge_sessions(now: float) -> None:
    expired = [key for key, value in _sessions.items() if now - value.last_accessed_at > SESSION_TTL_SECONDS]
    for key in expired:
        _sessions.pop(key, None)


def create_segmentation(library_id: str, raw: bytes) -> dict[str, Any]:
    image = decode_image(raw)
    started = time.perf_counter()
    features, input_size = get_runtime().encode(image)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    now = time.time()
    session_id = uuid.uuid4().hex
    session = SegmentationSession(
        library_id=library_id,
        features=features,
        original_size=image.shape[:2],
        input_size=input_size,
        created_at=now,
        last_accessed_at=now,
    )
    with _sessions_lock:
        _purge_sessions(now)
        while len(_sessions) >= MAX_SESSIONS:
            _sessions.popitem(last=False)
        _sessions[session_id] = session
    return {
        "session_id": session_id,
        "width": image.shape[1],
        "height": image.shape[0],
        "model": MODEL_NAME,
        "encoding_ms": elapsed_ms,
        "expires_in": SESSION_TTL_SECONDS,
    }


def predict_segmentation(
    library_id: str,
    session_id: str,
    points: list[dict[str, Any]],
) -> tuple[bytes, float]:
    if not points:
        raise SegmentationError("请至少添加一个主体点。")
    now = time.time()
    with _sessions_lock:
        _purge_sessions(now)
        session = _sessions.get(session_id)
        if not session or session.library_id != library_id:
            raise SegmentationSessionNotFound("智能选区已过期，请重新初始化。")
        session.last_accessed_at = now
        _sessions.move_to_end(session_id)
    mask, score = get_runtime().decode(session, points)
    ok, encoded = cv2.imencode(".png", mask)
    if not ok:
        raise SegmentationError("智能选区 Mask 生成失败。")
    return encoded.tobytes(), score


def delete_segmentation(library_id: str, session_id: str) -> bool:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session or session.library_id != library_id:
            return False
        _sessions.pop(session_id, None)
        return True
