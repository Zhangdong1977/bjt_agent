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

    # 运营台用量同步：机器对机器鉴权（静态 API Key + 可选 IP 白名单）
    # 运营台与本端共享 usage_sync_api_key；运营台不使用用户 JWT。
    usage_sync_api_key: str = ""          # env: USAGE_SYNC_API_KEY，必填（运营台与本端共享）
    usage_sync_ip_allowlist: str = ""     # env: USAGE_SYNC_IP_ALLOWLIST，逗号分隔，可选

    # Database
    database_url: str = ""  # Must be set via environment variable

    # Connection pool tuning (for PgBouncer cluster deployment)
    db_use_pgbouncer: bool = False  # Set true when connecting through PgBouncer (env: DB_USE_PGBOUNCER)
    db_pool_size: int = 10  # SQLAlchemy pool_size (env: DB_POOL_SIZE, PgBouncer: 5)
    db_max_overflow: int = 20  # SQLAlchemy max_overflow (env: DB_MAX_OVERFLOW, PgBouncer: 5)

    # Redis
    redis_url: str = ""  # Must be set via environment variable

    # RAG Memory Service
    rag_memory_service_url: str = "http://localhost:3001"

    # OCR Service (empty = local RapidOCR, url = remote microservice)
    ocr_service_url: str = ""
    ocr_model_dir: Path = Path(__file__).parent.parent / "models" / "RapidOcr"

    # 图像理解引擎切换：minimax（默认，MiniMax MCP VLM）/ baidu（百度云 OCR）/ volcengine（火山视觉）
    # env: IMAGE_UNDERSTANDING_PROVIDER。决定哪个后端实现 understand_image 工具。
    image_understanding_provider: str = "minimax"

    # 百度云 OCR（通用文字识别-高精度版 accurate_basic）
    baidu_ocr_app_id: str = ""  # env: BAIDU_OCR_APP_ID
    baidu_ocr_api_key: str = ""  # env: BAIDU_OCR_API_KEY
    baidu_ocr_secret_key: str = ""  # env: BAIDU_OCR_SECRET_KEY
    baidu_ocr_endpoint: str = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"

    # LLM Provider: "minimax", "volcengine", or "deepseek"
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

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_api_base: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-flash"

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

    # Agent Token Limit (上下文压缩触发阈值，DeepSeek/MiniMax 支持 1M 上下文)
    agent_token_limit: int = 800000  # 800K tokens, 环境变量: AGENT_TOKEN_LIMIT

    # Agent Progress Watchdog
    agent_progress_timeout: int = 600  # Max seconds without SSE events before task is considered hung (env: AGENT_PROGRESS_TIMEOUT)

    # Agent Total Timeout (absolute hard ceiling, independent of event stream).
    # Normal tasks take 22-35 min; this is the final backstop that terminates a
    # stuck task no matter what. Coordinates with Celery soft_time_limit/time_limit.
    agent_total_timeout: int = 5400  # Absolute max seconds for a review task, 90min (env: AGENT_TOTAL_TIMEOUT)

    # Heartbeat Fail-Closed
    heartbeat_fail_threshold: int = 3  # Consecutive heartbeat check failures before fail-closed (x5s poll ≈ 15s tolerance) (env: HEARTBEAT_FAIL_THRESHOLD)

    # Experience Self-Learning
    experience_injection_enabled: bool = False
    experience_max_inject: int = 3
    experience_maturity_threshold: float = 0.6
    experience_confidence_retire: float = 0.1
    experience_quality_threshold: float = 0.5

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
