"""Paragraph-range chunking for full-document VSTO scans (ADR-0003).

``word_check_text_style`` / ``word_check_paragraph_format`` walk every body
paragraph through Word COM, so their cost grows linearly with the document.  A
466-page bid document needs minutes for one call, which no fixed per-call
timeout can accommodate without also hiding a dead bridge for just as long.

This wrapper keeps the whole-document contract that the deterministic phase
and the LLM already rely on, but pays for it in bounded paragraph windows:

* one probe window measures the real per-paragraph cost on this machine, and
  every later window is sized to a ~25 s target;
* a window that fails is retried once at half size, then recorded as an
  explicit uncovered range instead of failing the whole dimension;
* results are merged back into the same shape VSTO returns (violations,
  coverage contract, echoed ``expected``), so echo verification and coverage
  reporting need no changes;
* an old plugin that ignores the range arguments echoes ``whole_document``
  on the first window, and the wrapper simply returns that as the result.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from typing import Any, Callable

from backend.agent.tools.vsto_remote import VstoRemoteTool
from backend.utils.mini_agent_utils import setup_mini_agent_path

setup_mini_agent_path()

from mini_agent.tools.base import Tool, ToolResult  # noqa: E402

logger = logging.getLogger(__name__)

# 观测基线（2026-09-17 生产）：~1,500 段/30 s；不超过该规模的文档一次调用即可。
SINGLE_SHOT_MAX_PARAGRAPHS = 2000
PROBE_CHUNK_PARAGRAPHS = 800
MIN_CHUNK_PARAGRAPHS = 300
MAX_CHUNK_PARAGRAPHS = 4000
TARGET_CHUNK_SECONDS = 25.0
CHUNK_TIMEOUT_SECONDS = 120
MERGED_MAX_VIOLATIONS = 300
_RANGE_KEYS = ("paragraph_start", "paragraph_end")
# 这些失败说明继续分块没有意义（文档变了 / 会话没了 / 任务取消 / 熔断）。
_FATAL_ERROR = re.compile(r"快照|文档已切换|文档已修改|会话已关闭|会话尚未|已取消|不再发起|no longer active|session")


def _memo_key(arguments: dict[str, Any]) -> str:
    stable = {k: v for k, v in arguments.items() if k != "snapshot_id" and k not in _RANGE_KEYS}
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


class ChunkedVstoTool(Tool):
    """Whole-document VSTO scan executed as bounded paragraph windows."""

    def __init__(
        self,
        *,
        inner: VstoRemoteTool,
        event_callback: Callable[[str, dict[str, Any]], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._event_callback = event_callback
        self._clock = clock
        self.total_paragraphs: int | None = None
        self.chunking_enabled = True
        self.chunk_timeout_seconds = CHUNK_TIMEOUT_SECONDS
        self._memo: dict[str, dict[str, Any]] = {}
        self.last_run: dict[str, Any] = {}

    # ---- Tool contract -------------------------------------------------
    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def description(self) -> str:
        return self._inner.description

    @property
    def parameters(self) -> dict[str, Any]:
        # 模型看到的仍是“全文”工具：范围参数由本包装器内部决定，不暴露给模型。
        schema = self._inner.parameters
        properties = dict(schema.get("properties") or {})
        for key in _RANGE_KEYS:
            properties.pop(key, None)
        schema["properties"] = properties
        return schema

    @property
    def inner(self) -> VstoRemoteTool:
        return self._inner

    def configure(self, *, total_paragraphs: int | None, chunking_enabled: bool = True) -> None:
        self.total_paragraphs = int(total_paragraphs) if total_paragraphs else None
        self.chunking_enabled = bool(chunking_enabled)

    async def execute(self, **kwargs) -> ToolResult:
        return VstoRemoteTool.to_tool_result(await self.request_raw(kwargs))

    async def request_raw(self, arguments: dict[str, Any], *, use_cache: bool = True) -> dict[str, Any]:
        args = {k: v for k, v in dict(arguments or {}).items() if k not in _RANGE_KEYS}
        key = _memo_key(args)
        if use_cache and key in self._memo:
            cached = json.loads(json.dumps(self._memo[key]))
            cached["cached"] = True
            if not cached.get("success"):
                cached["error"] = "（本次检查已用相同参数调用过该工具，未再次执行）" + str(cached.get("error") or "")
            return cached
        total = self.total_paragraphs
        if not self.chunking_enabled or not total or total <= SINGLE_SHOT_MAX_PARAGRAPHS:
            result = await self._inner.request_raw(args, use_cache=use_cache)
            self.last_run = {"mode": "single", "total_paragraphs": total}
        else:
            result = await self._run_chunked(args, total)
        self._memo[key] = json.loads(json.dumps({k: v for k, v in result.items() if k != "cached"}))
        return result

    # ---- Chunk loop ----------------------------------------------------
    async def _call(
        self, args: dict[str, Any], start: int, end: int, *, probe: bool = False
    ) -> tuple[dict[str, Any], float]:
        chunk_args = dict(args, paragraph_start=start, paragraph_end=end)
        # 首个窗口可能落在不认识范围参数的旧插件上（它会扫全文），所以按整篇文档的
        # 自适应超时等待；插件回显了范围之后，后续窗口才用短的分块超时。
        timeout = self.chunk_timeout_seconds
        if probe and self._inner.default_timeout_seconds:
            timeout = max(timeout, int(self._inner.default_timeout_seconds))
        began = self._clock()
        result = await self._inner.request_raw(chunk_args, timeout_seconds=timeout, use_cache=False)
        return result, max(0.0, self._clock() - began)

    async def _recover_late(self, result: dict[str, Any]) -> dict[str, Any] | None:
        if not result.get("expired") or not result.get("call_id"):
            return None
        broker = getattr(self._inner, "broker", None)
        fetch = getattr(broker, "fetch_late_result", None)
        if fetch is None:
            return None
        try:
            late = fetch(result.get("call_id"))
            if inspect.isawaitable(late):
                late = await late
        except Exception:
            logger.exception("late-result lookup failed for %s", self.name)
            return None
        return late if isinstance(late, dict) and late.get("success") else None

    @staticmethod
    def _is_fatal(result: dict[str, Any]) -> bool:
        if result.get("circuit_open"):
            return True
        return bool(_FATAL_ERROR.search(str(result.get("error") or "")))

    @staticmethod
    def _echoes_whole_document(data: dict[str, Any], requested_end: int, total: int) -> bool:
        scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
        if not scope:
            # 没有 scope 回显的插件版本不认识范围参数：结果按全文对待。
            return True
        if str(scope.get("kind") or "") == "whole_document":
            return True
        echoed_end = scope.get("paragraph_end")
        return isinstance(echoed_end, int) and requested_end < total and echoed_end >= total

    async def _emit_progress(self, *, checked: int, total: int, chunks: int, gaps: int) -> None:
        if self._event_callback is None:
            return
        try:
            outcome = self._event_callback(
                "vsto_tool_progress",
                {
                    "tool": self.name,
                    "checked_count": checked,
                    "cursor": checked,
                    "total": total,
                    "chunks": chunks,
                    "gaps": gaps,
                },
            )
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:
            logger.exception("chunk progress callback failed for %s", self.name)

    async def _run_chunked(self, args: dict[str, Any], total: int) -> dict[str, Any]:
        cursor = 1
        chunk_size = min(PROBE_CHUNK_PARAGRAPHS, total)
        chunks: list[dict[str, Any]] = []
        gaps: list[tuple[int, int]] = []
        while cursor <= total:
            end = min(total, cursor + chunk_size - 1)
            probe = not chunks and not gaps
            result, elapsed = await self._call(args, cursor, end, probe=probe)
            if not result.get("success"):
                if self._is_fatal(result):
                    self.last_run = {"mode": "chunked", "aborted": True, "chunks": len(chunks)}
                    return result
                late = await self._recover_late(result)
                if late is not None:
                    result = late
                else:
                    retry_size = max(MIN_CHUNK_PARAGRAPHS, chunk_size // 2)
                    end = min(total, cursor + retry_size - 1)
                    result, elapsed = await self._call(args, cursor, end, probe=probe)
                    if not result.get("success"):
                        if self._is_fatal(result) or probe:
                            # 首个窗口两次都失败：分不清是旧插件扫全文超时还是桥已失联，
                            # 继续逐窗口撞墙只会烧光预算，按整体失败返回交给上层降级。
                            self.last_run = {"mode": "chunked", "aborted": True, "chunks": len(chunks)}
                            return result
                        gaps.append((cursor, end))
                        cursor = end + 1
                        chunk_size = retry_size
                        await self._emit_progress(checked=end, total=total, chunks=len(chunks), gaps=len(gaps))
                        continue
                    chunk_size = retry_size
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            if not chunks and not gaps and self._echoes_whole_document(data, end, total):
                # 旧插件忽略范围参数并扫描了全文：这一次返回就是完整结果。
                self.last_run = {"mode": "legacy_whole_document", "total_paragraphs": total}
                return result
            chunks.append({"start": cursor, "end": end, "seconds": elapsed, "data": data})
            await self._emit_progress(checked=end, total=total, chunks=len(chunks), gaps=len(gaps))
            paragraphs = end - cursor + 1
            if elapsed > 0 and paragraphs > 0:
                per_paragraph = elapsed / paragraphs
                if per_paragraph > 0:
                    chunk_size = _clamp(int(TARGET_CHUNK_SECONDS / per_paragraph), MIN_CHUNK_PARAGRAPHS, MAX_CHUNK_PARAGRAPHS)
            cursor = end + 1
        merged = self._merge(chunks, gaps, total)
        self.last_run = {
            "mode": "chunked",
            "chunks": len(chunks),
            "gaps": gaps,
            "seconds": round(sum(item["seconds"] for item in chunks), 1),
            "total_paragraphs": total,
        }
        return merged

    # ---- Merge ---------------------------------------------------------
    def _merge(self, chunks: list[dict[str, Any]], gaps: list[tuple[int, int]], total: int) -> dict[str, Any]:
        if not chunks:
            reason = "；".join(f"第 {s}–{e} 段未能读取" for s, e in gaps) or "没有任何段落范围被成功读取"
            return {"success": False, "data": {}, "content": "", "error": f"Word 全文扫描未能完成：{reason}"}
        first = chunks[0]["data"]
        merged: dict[str, Any] = {k: v for k, v in first.items() if k not in {"violations", "unknown_reasons"}}
        violations: list[Any] = []
        reasons: list[str] = []
        checked_count = 0
        checked_characters = 0
        checked_paragraphs = 0
        violation_count = 0
        complete = True
        truncated = False
        for chunk in chunks:
            data = chunk["data"]
            items = data.get("violations") if isinstance(data.get("violations"), list) else []
            violations.extend(items)
            for reason in data.get("unknown_reasons") or []:
                text = str(reason)
                if text and text not in reasons:
                    reasons.append(text)
            checked_count += _int(data.get("checked_count"))
            checked_characters += _int(data.get("checked_characters"))
            checked_paragraphs += _int(data.get("checked_paragraphs"))
            violation_count += _int(data.get("violation_count"), fallback=len(items))
            if str(data.get("coverage") or "partial") != "complete":
                complete = False
            if data.get("truncated") is True:
                truncated = True
        for start, end in gaps:
            complete = False
            reasons.append(f"第 {start}–{end} 段未能读取（Word 工具调用未成功），该范围未检查")
        if len(violations) > MERGED_MAX_VIOLATIONS:
            violations = violations[:MERGED_MAX_VIOLATIONS]
            truncated = True
            reasons.append(f"违规条目超过 {MERGED_MAX_VIOLATIONS} 条，仅保留前 {MERGED_MAX_VIOLATIONS} 条明细")
        merged.update(
            {
                "violations": violations,
                "unknown_reasons": reasons,
                "checked_count": checked_count,
                "violation_count": violation_count,
                "coverage": "complete" if complete else "partial",
                "truncated": truncated,
                "scope": {
                    "kind": "whole_document",
                    "paragraph_start": 1,
                    "paragraph_end": total,
                    "document_paragraph_count": total,
                    "scope_complete": not gaps,
                    "chunks": len(chunks),
                    "uncovered_ranges": [[s, e] for s, e in gaps],
                },
            }
        )
        if checked_characters:
            merged["checked_characters"] = checked_characters
        if checked_paragraphs:
            merged["checked_paragraphs"] = checked_paragraphs
        return {
            "success": True,
            "data": merged,
            "content": json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
            "error": None,
            "snapshot_id": first.get("snapshot_id"),
        }


def _int(value: Any, fallback: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return fallback
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback
