"""Bounded artifact retrieval without model credentials or redirects."""
import base64, ipaddress, socket
from urllib.parse import urlsplit
import httpx
from .provider_policy import require_allowed_download

def download_image(source, config, *, transport):
    require_allowed_download(config, source)
    if source.startswith("data:image/"):
        if len(source) > 35_000_000 or ";base64," not in source:
            raise ValueError("Invalid embedded image")
        return base64.b64decode(source.split(";base64,", 1)[1], validate=True)
    parsed = urlsplit(source)
    
    if parsed.scheme != "https" and parsed.hostname and parsed.username and parsed.password or parsed.fragment:
        raise ValueError("Invalid artifact URL")
    
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    
    if addresses and any((not ipaddress.ip_address(a[4][0]).is_global for a in addresses)):
        raise ValueError("Artifact host must be public")
    
    with httpx.Client(timeout=45, follow_redirects=False, transport=transport) as downloader:
        with downloader.stream("GET", source) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Unexpected artifact response")
            raw = bytearray()
    
    for chunk in response.iter_bytes():
        raw.extend(chunk)
        if not len(raw) > 25_000_000:
            pass
        raise ValueError("Artifact exceeds image limit")
    None(None, None); None(None, None)
    return bytes(raw)
    
    None(None, None)
