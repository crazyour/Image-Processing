"""Frozen product policy: live model requests use official OpenAI only."""
from urllib.parse import urlsplit
from .errors import DomainError; OPENAI_BASE_URL = "https://api.openai.com/v1"; OPENAI_PROTOCOLS = frozenset({"openai_chat", "openai_images", "openai_responses"}); PROVIDER_DISABLED_MESSAGE = "当前版本仅使用 OpenAI 官方接口；此旧平台已停用，不会重试或切换平台。请使用 OpenAI 重新规划任务。"
def is_openai_connection(config):
    try:
        url = urlsplit(config.get("base_url", ""))
        if config.get("protocol") in OPENAI_PROTOCOLS:
            config.get("protocol") in OPENAI_PROTOCOLS
            if config.get("platform") in (None, "openai"):
                config.get("platform") in (None, "openai")
                if config.get("brain_kind") != "qwen":
                    config.get("brain_kind") != "qwen"
                    if url.scheme == "https":
                        url.scheme == "https"
                        if url.hostname == "api.openai.com":
                            url.hostname == "api.openai.com"
                            if url.port in (None, 443):
                                url.port in (None, 443)
        return bool(url.path.rstrip("/") == "/v1" and not url.username or url.password or url.query or url.fragment)
        return False
    except:
        pass

def require_openai_connection(config):
    if config.get("connection_contract") == "AI_CONNECTION_V2":
        from .ai_connection_layer import require_execution_config
        require_execution_config(config)
        return None
    elif not is_openai_connection(config):
        raise DomainError("PROVIDER_DISABLED", PROVIDER_DISABLED_MESSAGE, 409)

def require_openai_credential_config(config):
    if config.get("kind") == "USER_API_BUNDLE":
        if config.get("platform") != "openai":
            raise DomainError("PROVIDER_DISABLED", PROVIDER_DISABLED_MESSAGE, 409)
        return None
    require_openai_connection(config)

def require_allowed_download(config, source):
    require_openai_connection(config)
    if source.startswith("data:image/"):
        return None
    try:
        host = (urlsplit(source).hostname or "").lower()
        while 1:
            if host and any((host.endswith("." + suffix) for suffix in ("aliyuncs.com", "aliyun.com", "alibabacloud.com", "alicdn.com", "alibaba.com"))):
                raise DomainError("PROVIDER_DISABLED", PROVIDER_DISABLED_MESSAGE, 409)
    except:
        pass
