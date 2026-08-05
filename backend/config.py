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

    # Operate platform integration. Must be set per environment; do not fall
    # back to production from dev/pre-release.
    operate_api_base_url: str = ""
    operate_api_timeout_seconds: float = 5.0

    # operate-two 充值桥接内部接口共享密钥（X-Internal-Token）。env: OPERATE_INTERNAL_TOKEN
    # 须与 operate-two application-*.yml 的 document.bocom.internalToken 同值；为空则真实支付路径不可用。
    operate_internal_token: str = ""

    # 充值套餐全部使用真实交行支付；以下配置只控制套餐可见性。
    billing_hidden_package_codes: str = ""   # env: BILLING_HIDDEN_PACKAGE_CODES，逗号分隔，prod="test"
    # 测试套餐（code="test"）显式开关。默认 False——fail-closed：即使漏配
    # BILLING_HIDDEN_PACKAGE_CODES，prod 也不会暴露 1 分钱测试套餐。dev 在 .env 设 true 开启。
    billing_test_package_enabled: bool = False  # env: BILLING_TEST_PACKAGE_ENABLED
    # A user may run only this many billable top-level tasks at once.  This is
    # separate from the sub-agent concurrency inside one task.
    billing_max_active_tasks_per_user: int = 1
    # Terminal tasks whose worker disappeared are finalized by reconciliation
    # after this grace period, allowing late usage writes to land first.
    billing_orphan_finalize_grace_seconds: int = 300

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

    # Duplicate S2 selective image/OCR channel.  Hashing is always local;
    # OCR/VLM calls are separately switchable and bounded so an upload never
    # fans out into unmetered paid calls.
    duplicate_ocr_enabled: bool = True
    duplicate_remote_ocr_enabled: bool = False
    duplicate_vision_enabled: bool = False
    duplicate_ocr_max_images: int = 24
    duplicate_remote_ocr_max_calls: int = 4
    duplicate_vision_max_calls: int = 2
    duplicate_ocr_min_local_confidence: float = 0.72
    duplicate_scan_text_threshold: int = 30

    # S2-4 release switches and calibrated thresholds.  Batch mode remains
    # opt-in until the pre-release acceptance gate is signed off.
    duplicate_batch_enabled: bool = False
    duplicate_algorithm_version: str = "duplicate-s2-4.2"
    duplicate_candidate_min_score: float = 0.45
    duplicate_lexical_min_score: float = 0.16
    duplicate_structure_min_score: float = 0.50
    duplicate_near_exact_min_score: float = 0.72
    duplicate_image_min_score: float = 0.78
    duplicate_pair_max_candidates: int = 400
    duplicate_batch_max_candidates: int = 1200

    # S2-2B semantic recall is opt-in until S2-4 calibration approves the
    # thresholds.  Turning it off leaves the deterministic S2-1 channels in
    # place and performs zero embedding-provider calls.
    duplicate_semantic_enabled: bool = False
    # Semantic recall provider is independent from the main LLM provider.
    # ``llm`` performs one bounded task-local semantic clustering call and is
    # the safe default when no dedicated embedding endpoint is provisioned.
    duplicate_semantic_provider: Literal["llm", "minimax", "volcengine"] = "llm"
    duplicate_embedding_batch_size: int = 32
    duplicate_embedding_timeout_seconds: float = 45.0
    duplicate_embedding_max_blocks: int = 400
    duplicate_embedding_max_input_chars: int = 500_000
    duplicate_embedding_min_chars: int = 24
    duplicate_semantic_min_score: float = 0.72
    duplicate_embedding_breaker_failures: int = 3
    duplicate_embedding_breaker_cooldown_seconds: int = 120

    # LLM Provider: "minimax", "volcengine", or "deepseek"
    llm_provider: str = "minimax"

    # MiniMax
    mini_agent_api_key: str = ""
    mini_agent_api_base: str = "https://api.minimaxi.com"
    mini_agent_model: str = "MiniMax-M2.7-highspeed"
    minimax_embedding_model: str = "embo-01"

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
    duplicate_rule_library_dir: Path = (
        Path(__file__).parent.parent / "docs" / "rules-duplicate"
    )

    @property
    def project_root(self) -> Path:
        """Get the backend project root."""
        return Path(__file__).resolve().parent.parent

    @property
    def rule_library_path(self) -> Path:
        """Get an existing rule library path for the current runtime.

        Local Windows development can inherit the Linux deployment path from
        .env. When that configured path is not available, fall back to this
        worktree's bundled rules.
        """
        configured = self.rule_library_dir
        configured_path = configured if configured.is_absolute() else self.project_root / configured
        configured_path = configured_path.resolve()

        if configured_path.exists() and configured_path.is_dir():
            return configured_path

        default_path = (self.project_root / "docs" / "rules").resolve()
        configured_text = configured.as_posix().replace("\\", "/")
        if configured_text.endswith("/docs/rules") and default_path.exists() and default_path.is_dir():
            return default_path

        return configured_path

    @property
    def duplicate_rule_library_path(self) -> Path:
        """Resolve the technical-bid duplicate-check rule directory."""
        configured = self.duplicate_rule_library_dir
        configured_path = configured if configured.is_absolute() else self.project_root / configured
        configured_path = configured_path.resolve()
        if configured_path.exists() and configured_path.is_dir():
            return configured_path

        default_path = (self.project_root / "docs" / "rules-duplicate").resolve()
        configured_text = configured.as_posix().replace("\\", "/")
        if configured_text.endswith("/docs/rules-duplicate"):
            return default_path
        return configured_path

    @property
    def knowledge_base_path(self) -> Path:
        """Get absolute knowledge base path."""
        if self.knowledge_base_dir.is_absolute():
            return self.knowledge_base_dir
        return Path(__file__).parent.parent / self.knowledge_base_dir

    # File Upload
    max_upload_size_mb: int = 1024  # Single-file limit: 1 GiB
    max_upload_size_bytes: int = 1024 * 1024 * 1024  # 1 GiB in bytes

    # 单连接上传限速（字节/秒）；0 表示不限速。env: UPLOAD_BYTES_PER_SEC。
    # 后端流式分块读取 + 时间补偿实现：因 nginx proxy_request_buffering off，
    # 后端读慢会 TCP 反压到浏览器，端到端限速成立。默认 4 MiB/s（1GiB ≈ 256s）。
    # 集群并发上限由 nginx limit_conn 控制，见 deploy/nginx/bjt-cluster。
    upload_bytes_per_sec: int = 4 * 1024 * 1024  # 4 MB/s

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

    # Sub-Agent 最大执行步数（单项检查脑容量上限，brain_capacity = actual_steps / max_steps × 100%）
    sub_agent_max_steps: int = 500  # 环境变量: SUB_AGENT_MAX_STEPS

    # LLM 单次调用超时与超时重试（防偶发卡顿毁掉整个子任务）。
    # 生产实测：deepseek-v4-flash 偶发单次调用卡死 180s，原实现直接放弃整个子任务（已积累的
    # 数十步核实成果全丢）。改为超时后重试 N 次，挽救偶发性卡顿。注意与 httpx 客户端 timeout
    # (120s, bid_review_agent.py create_llm_client) 的关系：httpx 先超时会触发内层 async_retry；
    # 此处覆盖的是 asyncio.timeout(180s) 触发的更外层 TimeoutError（httpx 未触发的卡死场景）。
    llm_call_timeout: int = 180  # 单次 LLM 调用的 asyncio 超时秒数 (env: LLM_CALL_TIMEOUT)
    llm_timeout_max_retries: int = 2  # 超时后最多重试次数（总尝试 = 1 + 此值）(env: LLM_TIMEOUT_MAX_RETRIES)
    llm_timeout_retry_delay: float = 5.0  # 重试前固定等待秒数（给模型端恢复时间）(env: LLM_TIMEOUT_RETRY_DELAY)

    # Agent Progress Watchdog
    # 500 步任务下单步偶发慢（读大文档 + 长思考）需要更长容忍，避免误判卡死
    agent_progress_timeout: int = 1200  # Max seconds without SSE events before task is considered hung (env: AGENT_PROGRESS_TIMEOUT)

    # Agent Total Timeout (absolute hard ceiling, independent of event stream).
    # 500 步任务的绝对上界（平均 ~30s/步 ≈ 4.2h，6h 留充足余量）。这是最终兜底，
    # 无论事件流如何都会终止卡死任务。与 Celery soft_time_limit/time_limit 配合。
    agent_total_timeout: int = 21600  # Absolute max seconds for a review task, 6h (env: AGENT_TOTAL_TIMEOUT)

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

    # 集群节点期望清单（系统状态页用）。JSON 字符串，env: CLUSTER_NODE_SPECS。
    # 形如 [{"name":"node1","label":"节点1 (192.168.40.110)","roles":["review","parser"]}]。
    # 为空时仅展示实际响应的 worker；配置后整节点掉线也能显示为 offline。
    cluster_node_specs: str = ""
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
