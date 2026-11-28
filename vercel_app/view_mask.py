"""Validate local-edit masks and preserve pixels outside the selected region."""
from __future__ import annotations

import base64
import binascii
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from .workflow import WorkflowError, VIEW_NAMES, effective_prompt

MAX_MASK_DATA_URL = 4_000_000  # Keep the JSON request below the hosting body limit.
MAX_EDIT_PIXELS = 16_000_000


def _image(raw: bytes) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > MAX_EDIT_PIXELS:
            raise WorkflowError("EDIT_IMAGE_TOO_LARGE", "图片尺寸过大，无法局部修复", 422)
        return ImageOps.exif_transpose(image).convert("RGBA")


def validate_view_mask(data_url: str, source: bytes) -> bytes:
    """Use PNG alpha only: transparent = edit, opaque = preserve."""
    prefix = "data:image/png;base64,"
    if len(data_url) > MAX_MASK_DATA_URL or not data_url.startswith(prefix):
        raise WorkflowError("INVALID_VIEW_MASK", "遮罩必须是小于 3 MB 的 PNG 图片", 422)
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
        original = _image(source)
        with Image.open(io.BytesIO(raw)) as mask:
            if mask.format != "PNG" or mask.size != original.size or "A" not in mask.getbands():
                raise WorkflowError("INVALID_VIEW_MASK", "遮罩需带透明通道，且尺寸必须与当前视图一致", 422)
            # Snap antialiasing to a binary selection; fully transparent pixels
            # are the edit region in the provider API.
            alpha = mask.getchannel("A").point(lambda a: 0 if a < 128 else 255)
            if alpha.getextrema()[0] == 255:
                raise WorkflowError("EMPTY_VIEW_MASK", "请先涂抹需要修改的区域", 422)
            normalized = Image.new("RGBA", original.size, (0, 0, 0, 255))
            normalized.putalpha(alpha)
            output = io.BytesIO()
            normalized.save(output, "PNG")
            return output.getvalue()
    except (ValueError, binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise WorkflowError("INVALID_VIEW_MASK", "遮罩图片无效，请重新涂抹", 422) from exc


def composite_view_edit(source: bytes, generated: bytes, mask: bytes) -> bytes:
    original, edited = _image(source), _image(generated)
    if edited.size != original.size:
        # Same-aspect output may use a different provider resolution. Never
        # stretch an unexpected aspect ratio into the original view.
        if edited.width * original.height != edited.height * original.width:
            raise WorkflowError("EDIT_SIZE_MISMATCH", "修复结果比例与原图不一致，原图已保留", 502)
        edited = edited.resize(original.size, Image.Resampling.LANCZOS)
    with Image.open(io.BytesIO(mask)) as selection:
        editable = ImageOps.invert(selection.getchannel("A"))
        result = Image.composite(edited, original, editable)
    output = io.BytesIO()
    result.save(output, "PNG")
    return output.getvalue()


def masked_view_prompt(user_prompt: str, view: str, refinement: str,
                       reference_count: int, prior_views: list[str], view_mode: str) -> str:
    evidence = f"第 2 至 {reference_count + 1} 张是原始主体参考。" if reference_count else ""
    for index, prior in enumerate(prior_views, start=reference_count + 2):
        evidence += f"第 {index} 张是已生成的{VIEW_NAMES[prior]}，仅用于保持结构一致。"
    return (
        f"局部修复第一张图片（当前{VIEW_NAMES[view]}），不是重新生成整张视图。\n"
        "遮罩完全透明的区域是唯一可编辑区域。不透明区域必须保持不变。"
        "保持第一张图的画幅、相机方向、主体位置、姿势、大小及未选中的部件；"
        "不要旋转、镜像、移动或重新构图。仅在选区内按用户要求修改，并自然衔接周围。"
        "不得把遮罩、涂抹颜色或标记画进结果。\n"
        f"{evidence}\n{effective_prompt(user_prompt, view_mode)}\n"
        f"选区内以本次修复要求为准：\n{refinement.strip()}"
    )
