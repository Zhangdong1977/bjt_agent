"""Configuration management for the bid review agent backend."""

from pathlib import Path
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "Bid Review Agent"
    app_version: str = "1.0.0"
    debug: bool = False

    # API
    api_prefix: str = "/api"

    # Security
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: Literal["HS256", "HS512"] = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 1 day
    refresh_token_expire_days: int = 7  # 7 days

    # Database
    database_url: str = "postgresql+asyncpg://ssirs_user:y6+YufO6njlzxXiaNj6rA4xZaT3ofwT6@183.66.37.186:7004/bjt_agent"

    # Redis
    redis_url: str = "redis://183.66.37.186:7005/0"

    # RAG Memory Service
    rag_memory_service_url: str = "http://localhost:3001"

    # Mini-Agent
    mini_agent_api_key: str = ""
    mini_agent_api_base: str = "https://api.minimaxi.com"
    mini_agent_model: str = "MiniMax-M2.7-highspeed"

    # Workspace
    workspace_dir: Path = Path("./workspace")

    # Celery
    celery_broker_url: str = "redis://183.66.37.186:7005/0"
    celery_result_backend: str = "redis://183.66.37.186:7005/0"

    # Proxy
    http_proxy: str = "http://127.0.0.1:7890"
    https_proxy: str = "http://127.0.0.1:7890"

    @property
    def workspace_path(self) -> Path:
        """Get absolute workspace path."""
        if self.workspace_dir.is_absolute():
            return self.workspace_dir
        return Path(__file__).parent.parent / self.workspace_dir


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
