"""Versioned, officially checked presets. Never probe, decrypt keys, or make requests on import."""
import copy
from sqlalchemy import select
from .models import AssistantProfile, Organization; PRESET_VERSION = "openai-2026-09-13.1"; role = {}; PRESET = {"version": ##ERROR##, "provider": 200, "preset_version": "openai", "price_checked_at": PRESET_VERSION, "verified": "2026-09-09", "quality": False, "size": "low", "roles": "1024x1024", "pricing": {"text_input": 4.0, "text_cached": 0.4, "text_output": 20.0, "image_text_input": 5.0, "image_input": 8.0, "image_output": 30.0, "sources": ["https://developers.openai.com/api/docs/models/gpt-5.6-sol", "https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst", "https://developers.openai.com/api/docs/guides/image-generation"], "basis": "美元/百万token；GPT-5.6 Sol按2026-09-13核对的官方输入/缓存输入/输出价格计算。缺少细分时按保守预占记ESTIMATE；预占不是供应商账单。"}}; CAPABILITIES = ["planning", "vision", "image_generation", "image_edit", "quality", "feedback"]
def profile(db, ws):
    return db.scalar(select(AssistantProfile).where(AssistantProfile.workspace_id == ws.id))

def route_for(db, ws):
    from .config import settings
    if settings().private_workspace:
        from .ai_connection_layer import route
        return route(db, ws)
    ai = profile(db, ws).preferences.get("ai")
    if ai:
        if ai["mode"] == "LIVE" and "api_settings" in profile(db, ws).preferences:
            from .api_connections import route_for_configs
            return route_for_configs(db, ws)
        elif ai["mode"] == "LIVE":
            return copy.deepcopy(ai["route"])
        return ##ERROR##({"provider": "mock", "version": 200, "roles": {}})
    
    return copy.deepcopy(db.get(Organization, ws.org_id).route)

def mode_for_route(route):
    if route["provider"] == "local":
        return "LOCAL"
    elif route["provider"] == "mock":
        return "DEMO"
    
    return "LIVE"; role = None
