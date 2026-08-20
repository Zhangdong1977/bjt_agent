"""LLM client factory for multi-provider support."""

from backend.config import get_settings
from backend.utils.mini_agent_utils import setup_mini_agent_path

setup_mini_agent_path()

from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider


def create_llm_client(timeout: float | None = None) -> LLMClient:
    """Create LLM client based on settings.llm_provider.

    Args:
        timeout: Optional timeout in seconds for API calls.

    Returns:
        Configured LLMClient instance.
    """
    settings = get_settings()

    if settings.llm_provider == "volcengine":
        return LLMClient(
            api_key=settings.volcengine_api_key,
            provider=LLMProvider.OPENAI,
            api_base=settings.volcengine_api_base,
            model=settings.volcengine_model,
            reasoning_split=False,
            timeout=timeout,
        )

    if settings.llm_provider == "deepseek":
        return LLMClient(
            api_key=settings.deepseek_api_key,
            provider=LLMProvider.OPENAI,
            api_base=settings.deepseek_api_base,
            model=settings.deepseek_model,
            reasoning_split=False,
            reasoning_mode="deepseek",
            timeout=timeout,
        )

    # Tencent Cloud TokenHub: OpenAI 兼容协议，deepseek 系模型思考模式下同样
    # 返回 message.reasoning_content，复用 deepseek 的 reasoning 解析。
    if settings.llm_provider == "tencent":
        return LLMClient(
            api_key=settings.tencent_api_key,
            provider=LLMProvider.OPENAI,
            api_base=settings.tencent_api_base,
            model=settings.tencent_model,
            reasoning_split=False,
            reasoning_mode="deepseek",
            timeout=timeout,
        )

    # Default: MiniMax
    return LLMClient(
        api_key=settings.mini_agent_api_key,
        provider=LLMProvider.OPENAI,
        api_base=settings.mini_agent_api_base,
        model=settings.mini_agent_model,
        reasoning_split=True,
        timeout=timeout,
    )
