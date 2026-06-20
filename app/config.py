from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Telegram Message Scheduler"
    secret_key: str = "dev-secret-change-me"
    token_encryption_key: str = ""
    database_url: str = "sqlite:///./scheduler.db"
    default_timezone: str = "Asia/Baku"
    superadmin_username: str = "admin"
    superadmin_password: str = "admin12345"
    upload_dir: str = "uploads"
    max_upload_mb: int = 50
    vercel: bool = False
    cron_secret: str = ""
    cloudinary_cloud_name: str = ""
    cloudinary_upload_preset: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def fernet_key(self) -> bytes:
        if self.token_encryption_key:
            return self.token_encryption_key.encode()
        import base64
        import hashlib

        return base64.urlsafe_b64encode(hashlib.sha256(self.secret_key.encode()).digest())

    @property
    def uploads_path(self) -> Path:
        return Path(self.upload_dir)

    @property
    def is_serverless(self) -> bool:
        return self.vercel or bool(__import__("os").environ.get("VERCEL"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
fernet = Fernet(settings.fernet_key)
