from functools import lru_cache
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ART_", extra="ignore")
    database_url: str = "sqlite:///./var/art.db"
    storage_dir: Path = Path("var/private")
    environment: str = "development"
    secure_cookie: bool = False
    allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000"
    master_key: str = ""
    master_key_file: str = ""
    sandbox: bool = False
    schedule_enabled: bool = True
    live_enabled: bool = False
    
    @model_validator(mode="after")
    def sandbox_limits(self):
        if self.sandbox:
            self.live_enabled = False
            self.schedule_enabled = False
        return self
    
    lease_seconds: int = 600
    max_upload_bytes: int = 15_728_640
    max_pixels: int = 20_000_000
    frontend_dir: Path = Path("frontend/dist")
    desktop: bool = False
    private_workspace: bool = False
    bootstrap_token: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_vision_model: str = "gpt-4.1-mini"
    openai_image_model: str = "gpt-image-2.5-flare"
    openai_image_quality: str = "medium"
    meshy_api_key: str = ""
    meshy_base_url: str = "https://api.meshy.ai"
    hunyuan_api_key: str = ""
    hunyuan_base_url: str = "https://tokenhub.tencentmaas.com/v1"
    hunyuan_3d_model: str = "hy-3d-3.1"
    public_base_url: str = ""
    image_to_3d_asset_signing_key: str = ""
    image_to_3d_asset_url_ttl_seconds: int = 86400
    max_model_download_bytes: int = 262_144_000
    
    def encryption_key(self) -> bytes:
        if self.master_key_file:
            return Path(self.master_key_file).read_bytes().strip()
        elif self.master_key:
            return self.master_key.encode()
        raise ValueError("后端尚未配置独立加密主密钥")

@lru_cache
def settings():
    return Settings()
