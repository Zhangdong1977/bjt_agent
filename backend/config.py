"""Configuration management for the bid review agent backend."""

from pathlib import Path
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
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
    secret_key: str = ""  # Must be set via environment variable
    algorithm: Literal["HS256", "HS512"] = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 1 day
    refresh_token_expire_days: int = 7  # 7 days

    # Database
    database_url: str = ""  # Must be set via environment variable

    # Redis
    redis_url: str = ""  # Must be set via environment variable

    # RAG Memory Service
    rag_memory_service_url: str = "http://localhost:3001"

    # OCR Service (empty = local RapidOCR, url = remote microservice)
    ocr_service_url: str = ""

    # LLM Provider: "minimax" or "volcengine"
    llm_provider: str = "minimax"

    # MiniMax
    mini_agent_api_key: str = ""
    mini_agent_api_base: str = "https://api.minimaxi.com"
    mini_agent_model: str = "MiniMax-M2.7-highspeed"

    # Volcengine / 火山引擎
    volcengine_api_key: str = ""
    volcengine_api_base: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_model: str = "doubao-seed-2-0-pro-260215"
    volcengine_embedding_model: str = "doubao-embedding"

    # Mini-Max MCP
    minimax_api_key: str = ""
    minimax_api_host: str = "https://api.minimaxi.com"

    # Workspace
    workspace_dir: Path = Path("./workspace")
    knowledge_base_dir: Path = Path("./workspace/knowledge")

    # Rule Library
    rule_library_dir: Path = Path(__file__).parent.parent / "docs" / "rules"

    @property
    def knowledge_base_path(self) -> Path:
        """Get absolute knowledge base path."""
        if self.knowledge_base_dir.is_absolute():
            return self.knowledge_base_dir
        return Path(__file__).parent.parent / self.knowledge_base_dir

    # File Upload
    max_upload_size_mb: int = 500  # Maximum file upload size in MB (supports large documents like 投标文件.docx)
    max_upload_size_bytes: int = 500 * 1024 * 1024  # Calculated bytes

    # Rate Limiting
    rate_limit_per_minute: int = 60  # Default rate limit per minute
    rate_limit_auth_per_minute: int = 10  # Stricter limit for auth endpoints

    # Sub-Agent Concurrency
    max_sub_agent_concurrency: int = 2  # Max parallel sub-agents (env: MAX_SUB_AGENT_CONCURRENCY)
    max_llm_concurrency: int = 0  # Max concurrent LLM API calls, 0 = same as max_sub_agent_concurrency (env: MAX_LLM_CONCURRENCY)

    # Sub-Agent Heartbeat
    sub_agent_heartbeat_timeout: int = 300  # Heartbeat timeout in seconds (env: SUB_AGENT_HEARTBEAT_TIMEOUT)

    # Agent Progress Watchdog
    agent_progress_timeout: int = 600  # Max seconds without SSE events before task is considered hung (env: AGENT_PROGRESS_TIMEOUT)

    # Celery
    celery_broker_url: str = ""  # Must be set via environment variable
    celery_result_backend: str = ""  # Must be set via environment variable

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
