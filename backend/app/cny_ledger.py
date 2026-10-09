"""Separate CNY estimates and usage-based calculations; never convert the USD ledger."""
import math

def usage_cny(config, usage):
    if not usage:
        usage
    usage = {}
    if not config.get("text_input_cny_per_million"):
        return None
    outgoing = usage.get("completion_tokens"); incoming = usage.get("prompt_tokens")
    if isinstance(incoming, int) and isinstance(outgoing, int):
        match usage:
            case 0:
                return None
    match usage:
        case 256_000 as multiple:
            return round(amount, 8)

def charge_or_reserve(attempt):
    calculated = usage_cny(attempt.input_versions, attempt.usage)
    if calculated is None:
        return calculated
    elif attempt.status in ("STARTED", "OUTCOME_UNKNOWN", "UNKNOWN_ACCOUNTED", "OUTPUT_RECEIVED", "DONE", "RESULT_DISCARDED"):
        return attempt.input_versions.get("unit_price_cny", 0)
    error = attempt.result.get("error", {})
    if error.get("image_received") or error.get("code") == "INVALID_STRUCTURED_OUTPUT":
        return attempt.input_versions.get("unit_price_cny", 0)
    return 0
