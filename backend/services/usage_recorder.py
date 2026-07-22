"""用量记录器 — 组装一行 ai_usage_records + 异步落库 + 绝不影响业务。

设计原则：fire-and-forget。任何异常都吞掉（仅 warning 日志），绝不阻塞或打断
审查主流程。参考现有 `_write_llm_metrics` 的 `except: pass` 写法。

调用方：
- LLM：bid_review_agent.wrapped_generate 的 success/error/timeout 出口
- OCR：baidu_ocr.BaiduOcrTool.execute 的 success/error 出口
均从 usage_context.get_usage_context() 取归属，无上下文（脚本/测试）则不记。
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.config import get_settings
from backend.models import async_session_factory
from backend.models.ai_usage_record import AiUsageRecord
from backend.services.usage_context import get_usage_context
from backend.services.cost_calculator import estimate_cost
from backend.services.usage_summary import refresh_task_summary

logger = logging.getLogger(__name__)


def _resolve_llm_model(settings, provider: str) -> Optional[str]:
    """按当前 provider 取 settings 中配置的 model 名。"""
    if provider == "deepseek":
        return settings.deepseek_model
    if provider == "volcengine":
        return settings.volcengine_model
    if provider == "minimax":
        return settings.mini_agent_model
    return None


def record_llm_usage(
    *,
    response: Any = None,
    latency_ms: Optional[int],
    status: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """记录一次 LLM 调用。在 wrapped_generate 的 success/error/timeout 出口同步调用（非 async）。"""
    ctx = get_usage_context()
    if ctx is None:
        return  # 无上下文（如脚本/测试），不记

    settings = get_settings()
    provider = settings.llm_provider
    model = model or _resolve_llm_model(settings, provider)

    usage = getattr(response, "usage", None) if response is not None else None
    prompt_t = getattr(usage, "prompt_tokens", 0) or 0
    comp_t = getattr(usage, "completion_tokens", 0) or 0
    total_t = getattr(usage, "total_tokens", 0) or 0
    # DeepSeek 上下文缓存拆分（命中/未命中输入），非 deepseek provider 无此字段，getattr 兜底 0
    hit_t = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss_t = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    raw = None
    if usage is not None:
        try:
            raw = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
        except Exception:
            raw = None

    cost = estimate_cost(
        provider=provider, model=model,
        prompt_tokens=prompt_t, completion_tokens=comp_t,
        prompt_cache_hit_tokens=hit_t, prompt_cache_miss_tokens=miss_t,
        status=status,
    ) if status == "success" else None

    record = AiUsageRecord(
        usage_type="llm", provider=provider, model=model,
        prompt_tokens=prompt_t, completion_tokens=comp_t, total_tokens=total_t,
        prompt_cache_hit_tokens=hit_t, prompt_cache_miss_tokens=miss_t,
        latency_ms=latency_ms, status=status, error_code=error_code,
        error_message=error_message, raw_usage=raw, cost_cny=cost,
        # 归属来自 ctx：
        external_user_id=ctx.external_user_id, local_user_id=ctx.local_user_id,
        user_name=ctx.user_name, enterprise_name=ctx.enterprise_name,
        interior_user=ctx.interior_user, project_id=ctx.project_id,
        task_id=ctx.task_id, todo_id=ctx.todo_id,
        usage_date=datetime.now(timezone.utc).date(),
    )
    _spawn(_write_one(record))


def record_ocr_usage(
    *,
    provider: str,
    endpoint: str,
    status: str,
    latency_ms: Optional[int] = None,
    words_result_num: int = 0,
    image_size_bytes: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """记录一次 OCR 调用。在 BaiduOcrTool.execute 的 success/error 出口同步调用。"""
    ctx = get_usage_context()
    if ctx is None:
        return

    cost = estimate_cost(provider=provider, status=status) if status == "success" else None

    record = AiUsageRecord(
        usage_type="ocr", provider=provider, endpoint=endpoint,
        ocr_calls=1, ocr_images=1, ocr_words_result_num=words_result_num,
        image_size_bytes=image_size_bytes, latency_ms=latency_ms, status=status,
        error_code=error_code, error_message=error_message, cost_cny=cost,
        external_user_id=ctx.external_user_id, local_user_id=ctx.local_user_id,
        user_name=ctx.user_name, enterprise_name=ctx.enterprise_name,
        interior_user=ctx.interior_user, project_id=ctx.project_id,
        task_id=ctx.task_id, todo_id=ctx.todo_id,
        usage_date=datetime.now(timezone.utc).date(),
    )
    _spawn(_write_one(record))


def record_embedding_usage(*, response: Any = None, model: str | None = None, status: str) -> None:
    """Record one embedding request and include its input tokens in task cost."""
    ctx = get_usage_context()
    if ctx is None:
        return
    settings = get_settings()
    provider = settings.llm_provider
    usage = getattr(response, "usage", None) if response is not None else None
    prompt_t = getattr(usage, "prompt_tokens", 0) or getattr(usage, "total_tokens", 0) or 0
    total_t = getattr(usage, "total_tokens", 0) or prompt_t
    cost = estimate_cost(provider=provider, model=model, prompt_tokens=prompt_t,
                         completion_tokens=0, status=status) if status == "success" else None
    record = AiUsageRecord(
        usage_type="embedding", provider=provider, model=model,
        prompt_tokens=prompt_t, completion_tokens=0, total_tokens=total_t,
        status=status, cost_cny=cost,
        external_user_id=ctx.external_user_id, local_user_id=ctx.local_user_id,
        user_name=ctx.user_name, enterprise_name=ctx.enterprise_name,
        interior_user=ctx.interior_user, project_id=ctx.project_id,
        task_id=ctx.task_id, todo_id=ctx.todo_id,
        usage_date=datetime.now(timezone.utc).date(),
    )
    _spawn(_write_one(record))


def _spawn(coro) -> None:
    """在当前事件循环上 fire-and-forget；异常吞掉，绝不影响业务。"""
    try:
        loop = asyncio.get_running_loop()
        t = loop.create_task(coro)
        t.add_done_callback(lambda x: x.exception() if not x.cancelled() else None)
    except RuntimeError:
        logger.warning("[usage] no running loop, usage record dropped")


async def _write_one(record: AiUsageRecord) -> None:
    try:
        async with async_session_factory() as db:
            db.add(record)
            await db.commit()
        # 流水落库成功后，刷新该 task 的用量汇总行（fire-and-forget，非任务场景跳过）。
        # 幂等：refresh 内部用 ON CONFLICT DO UPDATE 绝对值覆盖，重复刷新无副作用。
        if record.task_id:
            _spawn(refresh_task_summary(record.task_id))
    except Exception as e:
        logger.warning(f"[usage] write failed (ignored): {e}")
