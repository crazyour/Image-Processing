"""Single source of truth for provider runtime configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


class SettingsError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SettingsError(f"尚未配置 {name}")
    return value


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    base_url: str
    vision_model: str
    image_model: str
    image_quality: str


@dataclass(frozen=True)
class HunyuanSettings:
    api_key: str
    base_url: str
    model: str


@lru_cache
def openai_settings() -> OpenAISettings:
    quality = _required("OPENAI_IMAGE_QUALITY")
    if quality not in {"low", "medium", "high", "xhigh", "max", "auto"}:
        raise SettingsError("OPENAI_IMAGE_QUALITY 配置无效")
    return OpenAISettings(
        api_key=_required("OPENAI_API_KEY"),
        base_url=_required("OPENAI_BASE_URL").rstrip("/"),
        vision_model=_required("OPENAI_VISION_MODEL"),
        image_model=_required("OPENAI_IMAGE_MODEL"),
        image_quality=quality,
    )


@lru_cache
def hunyuan_settings() -> HunyuanSettings:
    return HunyuanSettings(
        api_key=_required("HUNYUAN_API_KEY"),
        base_url=_required("HUNYUAN_BASE_URL").rstrip("/"),
        model=_required("HUNYUAN_3D_MODEL"),
    )
