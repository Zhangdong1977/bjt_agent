"""用量记录器 — 组装一行 ai_usage_records + 可确认刷新的异步落库。

正常调用不阻塞审查主流程；任务终态必须调用 ``flush_task_usage``，确认所有
流水已持久化后才允许计费。写入失败会重试，并使结算保持 retry。

调用方：
- LLM：bid_review_agent.wrapped_generate 的 success/error/timeout 出口
- OCR：baidu_ocr.BaiduOcrTool.execute 的 success/error 出口
均从 usage_context.get_usage_context() 取归属，无上下文（脚本/测试）则不记。
"""

import asyncio
import functools
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from backend.config import get_settings
from backend.models import async_session_factory
from backend.models.ai_usage_record import AiUsageRecord
from backend.services.usage_context import get_usage_context
from backend.services.cost_calculator import estimate_cost
from backend.services.usage_summary import refresh_task_summary

logger = logging.getLogger(__name__)

_pending_writes: dict[str, set[asyncio.Task]] = {}
_write_failures: dict[str, list[BaseException]] = {}


def _resolve_llm_model(settings, provider: str) -> Optional[str]:
    """按当前 provider 取 settings 中配置的 model 名。"""
    if provider == "deepseek":
        return settings.deepseek_model
    if provider == "tencent":
        return settings.tencent_model
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
    _spawn(record)


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
    _spawn(record)
    _report_private_cloud_ocr(ctx)


def _report_private_cloud_ocr(ctx) -> None:
    """私有云模式：OCR 按次上报共享配额池（fire-and-forget，失败不影响主流程）。"""
    try:
        from backend.services.quota_client import consume_ocr, is_private_cloud

        if not is_private_cloud():
            return
        loop = asyncio.get_running_loop()
        loop.create_task(
            consume_ocr(
                service_type="ocr_review",
                user_id=str(ctx.external_user_id or "") or None,
                user_name=ctx.user_name,
            )
        )
    except RuntimeError:
        pass  # 无事件循环（脚本/单测），跳过上报
    except Exception as exc:  # noqa: BLE001
        logger.warning("[quota] ocr report dispatch failed: %s", exc)


def record_embedding_usage(
    *,
    provider: str,
    model: str,
    status: str,
    latency_ms: Optional[int],
    input_count: int,
    input_chars: int,
    cache_hits: int = 0,
    error_message: Optional[str] = None,
) -> None:
    """Record one batch-level embedding call/cache event."""

    ctx = get_usage_context()
    if ctx is None:
        return
    input_tokens = max(0, (int(input_chars) + 3) // 4)
    cost = (
        estimate_cost(
            provider=provider,
            model=model,
            embedding_input_tokens=input_tokens,
            status=status,
        )
        if status == "success"
        else None
    )
    record = AiUsageRecord(
        usage_type="embedding",
        provider=provider,
        model=model,
        embedding_calls=1 if input_count > 0 else 0,
        embedding_inputs=max(0, int(input_count)),
        embedding_input_chars=max(0, int(input_chars)),
        embedding_input_tokens=input_tokens,
        embedding_cache_hits=max(0, int(cache_hits)),
        latency_ms=latency_ms,
        status=status,
        error_message=error_message,
        raw_usage={
            "input_count": input_count,
            "input_chars": input_chars,
            "cache_hits": cache_hits,
        },
        cost_cny=cost,
        external_user_id=ctx.external_user_id,
        local_user_id=ctx.local_user_id,
        user_name=ctx.user_name,
        enterprise_name=ctx.enterprise_name,
        interior_user=ctx.interior_user,
        project_id=ctx.project_id,
        task_id=ctx.task_id,
        todo_id=ctx.todo_id,
        usage_date=datetime.now(timezone.utc).date(),
    )
    _spawn(record)


def record_vision_usage(
    *,
    provider: str,
    model: str | None,
    status: str,
    latency_ms: Optional[int],
    image_size_bytes: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    error_message: Optional[str] = None,
) -> None:
    """Record a bounded VLM image call separately from text LLM calls."""

    ctx = get_usage_context()
    if ctx is None:
        return
    cost = estimate_cost(
        provider=provider,
        model=model,
        prompt_tokens=max(0, int(prompt_tokens)),
        completion_tokens=max(0, int(completion_tokens)),
        status=status,
    ) if status == "success" else None
    record = AiUsageRecord(
        usage_type="vision",
        provider=provider,
        model=model,
        vision_calls=1,
        vision_images=1,
        prompt_tokens=max(0, int(prompt_tokens)),
        completion_tokens=max(0, int(completion_tokens)),
        total_tokens=max(0, int(total_tokens)),
        image_size_bytes=image_size_bytes,
        latency_ms=latency_ms,
        status=status,
        error_message=error_message,
        cost_cny=cost,
        external_user_id=ctx.external_user_id,
        local_user_id=ctx.local_user_id,
        user_name=ctx.user_name,
        enterprise_name=ctx.enterprise_name,
        interior_user=ctx.interior_user,
        project_id=ctx.project_id,
        task_id=ctx.task_id,
        todo_id=ctx.todo_id,
        usage_date=datetime.now(timezone.utc).date(),
    )
    _spawn(record)


def instrument_llm_client(client):
    """Wrap a Mini-Agent LLM client with task-scoped durable usage metering."""
    if getattr(client, "_bjt_usage_instrumented", False):
        return client
    original = client.generate

    @functools.wraps(original)
    async def wrapped(*args, **kwargs):
        started = time.perf_counter()
        try:
            response = await original(*args, **kwargs)
        except asyncio.TimeoutError:
            record_llm_usage(
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="timeout",
                error_message="LLM request timed out",
            )
            raise
        except Exception as exc:
            record_llm_usage(
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="error",
                error_message=str(exc),
            )
            raise
        record_llm_usage(
            response=response,
            latency_ms=int((time.perf_counter() - started) * 1000),
            status="success",
        )
        return response

    client.generate = wrapped
    client._bjt_usage_instrumented = True
    return client


def _spawn(record: AiUsageRecord) -> None:
    """Schedule one durable write and track it until task finalization."""
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_write_one(record))
        task_id = record.task_id
        if task_id:
            _pending_writes.setdefault(task_id, set()).add(task)

        def _done(done: asyncio.Task) -> None:
            if task_id:
                pending = _pending_writes.get(task_id)
                if pending is not None:
                    pending.discard(done)
                    if not pending:
                        _pending_writes.pop(task_id, None)
            if done.cancelled():
                if task_id:
                    _write_failures.setdefault(task_id, []).append(
                        RuntimeError("usage write was cancelled")
                    )
                return
            exc = done.exception()
            if exc is not None:
                logger.error("[usage] durable write failed: task=%s error=%s", task_id, exc)
                if task_id:
                    _write_failures.setdefault(task_id, []).append(exc)

        task.add_done_callback(_done)
    except RuntimeError:
        logger.error("[usage] no running loop, usage record cannot be scheduled")
        if record.task_id:
            _write_failures.setdefault(record.task_id, []).append(
                RuntimeError("no running event loop for usage write")
            )


async def _write_one(record: AiUsageRecord) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            async with async_session_factory() as db:
                db.add(record)
                await db.commit()
            if record.task_id:
                await refresh_task_summary(record.task_id)
            return
        except Exception as exc:
            last_error = exc
            logger.warning(
                "[usage] write failed: task=%s attempt=%s/3 error=%s",
                record.task_id,
                attempt,
                exc,
            )
            if attempt < 3:
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
    raise RuntimeError(f"usage record failed after retries: {last_error}") from last_error


async def flush_task_usage(task_id: str) -> None:
    """Wait for every local write and surface failures to billing."""
    if not task_id:
        return
    while True:
        pending = list(_pending_writes.get(task_id, ()))
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
    failures = _write_failures.pop(task_id, [])
    if failures:
        raise RuntimeError(
            f"{len(failures)} usage write(s) failed; first error: {failures[0]}"
        )
