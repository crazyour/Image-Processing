"""Token pricing for every paid provider call.

Rates are USD per 1M tokens and follow https://platform.openai.com/docs/pricing.
A call is priced as ``tokens x rate`` so the token counters and the dollar
amounts in the usage ledger always describe the same request.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from vercel_app.settings import optional_float


@dataclass(frozen=True)
class ModelRates:
    """USD per 1M tokens for one provider model."""

    text_input: float
    image_input: float
    cached_input: float
    output: float
    family: str

    @property
    def is_image(self) -> bool:
        return self.family == "image"


# GPT Image bills reference images at the image rate and prompts at the text rate;
# text and vision models bill every input token at the model input rate.
MODEL_RATES: dict[str, ModelRates] = {
    "gpt-image-2.5": ModelRates(5.0, 8.0, 2.0, 30.0, "image"),
    "gpt-image-2": ModelRates(5.0, 8.0, 2.0, 30.0, "image"),
    "gpt-image-1.5": ModelRates(5.0, 8.0, 2.0, 32.0, "image"),
    "gpt-image-1": ModelRates(5.0, 10.0, 2.5, 40.0, "image"),
    "gpt-image-1-mini": ModelRates(2.0, 2.5, 0.25, 8.0, "image"),
    "gpt-6-astra": ModelRates(10.0, 10.0, 1.0, 50.0, "text"),
    "gpt-6.1-sol": ModelRates(2.0, 2.0, 0.10, 10.0, "text"),
    "gpt-6-sol": ModelRates(2.0, 2.0, 0.20, 10.0, "text"),
    "gpt-6-luna": ModelRates(0.10, 0.10, 0.01, 0.50, "text"),
    "gpt-5.6-sol": ModelRates(4.0, 4.0, 0.40, 20.0, "text"),
    "gpt-5.6-terra": ModelRates(2.0, 2.0, 0.20, 12.0, "text"),
    "gpt-5.6-luna": ModelRates(0.20, 0.20, 0.02, 1.20, "text"),
    "gpt-5.5": ModelRates(5.0, 5.0, 0.50, 30.0, "text"),
    "gpt-5.4": ModelRates(2.5, 2.5, 0.25, 15.0, "text"),
    "gpt-5.4-mini": ModelRates(0.75, 0.75, 0.075, 4.50, "text"),
    "gpt-5.4-nano": ModelRates(0.20, 0.20, 0.02, 1.25, "text"),
    "gpt-4.1-mini": ModelRates(0.40, 0.40, 0.10, 1.60, "text"),
    "gpt-4o-mini": ModelRates(0.15, 0.15, 0.075, 0.60, "text"),
}

SNAPSHOT_SUFFIX = re.compile(r"-(?:latest|\d{4}-\d{2}-\d{2})$")

PRICE_ENV_VARS = (
    "USAGE_PRICE_3D_PER_TASK",
    "USAGE_PRICE_IMAGE_PER_IMAGE",
    "USAGE_PRICE_IMAGE_INPUT_PER_MILLION",
    "USAGE_PRICE_IMAGE_OUTPUT_PER_MILLION",
    "USAGE_PRICE_IMAGE_TEXT_INPUT_PER_MILLION",
    "USAGE_PRICE_CACHED_INPUT_PER_MILLION",
    "USAGE_PRICE_VISION_INPUT_PER_MILLION",
    "USAGE_PRICE_VISION_OUTPUT_PER_MILLION",
)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def model_rates(model: str | None) -> ModelRates | None:
    """Find the published rates of a model, tolerating snapshots and variants."""
    name = (model or "").strip().lower()
    if not name:
        return None
    if name in MODEL_RATES:
        return MODEL_RATES[name]
    trimmed = SNAPSHOT_SUFFIX.sub("", name)
    if trimmed in MODEL_RATES:
        return MODEL_RATES[trimmed]
    for key in sorted(MODEL_RATES, key=len, reverse=True):
        if name.startswith(key) and not name[len(key)].isalnum():
            return MODEL_RATES[key]
    if "image" in name:
        # A newer GPT Image release keeps the same shape of pricing.
        return MODEL_RATES["gpt-image-2.5"]
    return None


def effective_rates(model: str | None) -> ModelRates | None:
    """Published model rates with the environment overrides applied."""
    rates = model_rates(model)
    if rates is None:
        # A model that is not in the table is still priced when the operator
        # configures per-million rates for it.
        input_rate = optional_float("USAGE_PRICE_VISION_INPUT_PER_MILLION", 0.0)
        output_rate = optional_float("USAGE_PRICE_VISION_OUTPUT_PER_MILLION", 0.0)
        if not input_rate and not output_rate:
            return None
        return ModelRates(input_rate, input_rate, input_rate, output_rate, "text")
    if rates.is_image:
        return ModelRates(
            text_input=optional_float(
                "USAGE_PRICE_IMAGE_TEXT_INPUT_PER_MILLION", rates.text_input),
            image_input=optional_float(
                "USAGE_PRICE_IMAGE_INPUT_PER_MILLION", rates.image_input),
            cached_input=optional_float(
                "USAGE_PRICE_CACHED_INPUT_PER_MILLION", rates.cached_input),
            output=optional_float(
                "USAGE_PRICE_IMAGE_OUTPUT_PER_MILLION", rates.output),
            family=rates.family,
        )
    return ModelRates(
        text_input=optional_float(
            "USAGE_PRICE_VISION_INPUT_PER_MILLION", rates.text_input),
        image_input=rates.image_input,
        cached_input=optional_float(
            "USAGE_PRICE_CACHED_INPUT_PER_MILLION", rates.cached_input),
        output=optional_float(
            "USAGE_PRICE_VISION_OUTPUT_PER_MILLION", rates.output),
        family=rates.family,
    )


def input_token_details(payload: object) -> tuple[float, float, float]:
    """Read the text / image / cached input split reported by the provider."""
    if not isinstance(payload, dict):
        return 0.0, 0.0, 0.0
    scopes: list[dict] = []
    nested = payload.get("usage")
    if isinstance(nested, dict):
        scopes.append(nested)
    scopes.append(payload)
    for scope in scopes:
        details = scope.get("input_tokens_details")
        if not isinstance(details, dict):
            continue
        text_tokens = _number(details.get("text_tokens"))
        image_tokens = _number(details.get("image_tokens"))
        cached_tokens = _number(details.get("cached_tokens"))
        if text_tokens or image_tokens or cached_tokens:
            return text_tokens, image_tokens, cached_tokens
    return 0.0, 0.0, 0.0


def token_cost(model: str | None, input_tokens: float, output_tokens: float,
               text_tokens: float = 0.0, image_tokens: float = 0.0,
               cached_tokens: float = 0.0) -> float | None:
    """Price one call as tokens x unit rate, or None when no rate applies."""
    rates = effective_rates(model)
    if rates is None:
        return None
    input_tokens = max(float(input_tokens), 0.0)
    output_tokens = max(float(output_tokens), 0.0)
    if not input_tokens and not output_tokens:
        return None
    cached = min(max(float(cached_tokens), 0.0), input_tokens)
    if rates.is_image:
        known_text = max(float(text_tokens), 0.0)
        known_image = max(float(image_tokens), 0.0)
        rest = max(input_tokens - known_text - known_image, 0.0)
        if known_text or known_image:
            text_count = known_text
            image_count = known_image + rest
        else:
            # Without a split the whole input is treated as reference images,
            # because GPT Image bills them at the image input rate.
            text_count = 0.0
            image_count = input_tokens
        # GPT Image calls go through the Images API, which does not cache input.
        cached = 0.0
    else:
        text_count = input_tokens - cached
        image_count = 0.0
    cost = (
        text_count * rates.text_input
        + image_count * rates.image_input
        + cached * rates.cached_input
        + output_tokens * rates.output
    ) / 1_000_000
    return round(cost, 6)


def pricing_signature() -> str:
    """Fingerprint of the price configuration, used to skip repeated work."""
    return "|".join(
        f"{name}={os.getenv(name, '').strip()}" for name in PRICE_ENV_VARS
    )


# Rough token counts used before a call is made and its tokens are known.
# A 1024x1024 reference image that GPT Image keeps at high fidelity.
REFERENCE_IMAGE_TOKENS = 4700
# The output tokens of a 1024x1024 medium quality image.
GENERATION_OUTPUT_TOKENS = 1100


def estimated_call_cost(model: str | None, reference_images: int = 1) -> float | None:
    """Rough cost of one call, used to reserve budget before it runs."""
    rates = effective_rates(model)
    if rates is None or not rates.is_image:
        return None
    input_tokens = max(reference_images, 0) * REFERENCE_IMAGE_TOKENS
    cost = (
        input_tokens * rates.image_input
        + GENERATION_OUTPUT_TOKENS * rates.output
    ) / 1_000_000
    return round(cost, 6)
