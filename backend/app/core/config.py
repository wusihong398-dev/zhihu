from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TOTOD 知乎运营助手"
    app_env: str = "production"
    app_secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = 720
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    account_data_root: Path = Path("/var/lib/totod/accounts")
    default_timezone: str = "Asia/Shanghai"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
