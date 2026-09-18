"""Broker between a blind-check agent and the VSTO WebView2 bridge.

The agent runs in a Celery worker while the VSTO bridge runs in the user's
Word process.  Calls are therefore persisted in PostgreSQL, announced through
the existing task SSE stream, and completed through a short-lived Redis result
key.  A small in-process fallback keeps protocol tests runnable without Redis.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import select

from backend.config import get_settings
from backend.models import BlindCheckTask, VstoToolCall, VstoToolSession
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

TOOL_SESSION_TTL_MINUTES = 30
TOOL_CALL_TIMEOUT_SECONDS = 120
# 活性判定（ADR-0003）：只要 VSTO 还在上报进度，就不按固定墙钟判超时；
# 只有“无进展”持续超过该值才放弃本次调用。旧插件不上报进度时退化为固定超时。
TOOL_CALL_IDLE_TIMEOUT_SECONDS = 45
# 单次调用含所有活性续期的绝对上限，也是 DB / SSE 里 ``expires_at`` 的口径。
TOOL_CALL_MAX_TIMEOUT_SECONDS = 600
# 同一任务内累计在无应答调用上浪费的秒数超过该值后，不再向 Word 发起新的调用：
# 防止“超时 → 模型再调 → 再超时”的重试放大把整个任务预算烧光。
TOOL_CALL_WASTED_BUDGET_SECONDS = 240
# 全文扫描类工具支持按正文段落范围分块调用；范围上限与 C# 侧 BlindMaxParagraphs 一致。
PARAGRAPH_RANGE_MAX = 1_000_000
_PARAGRAPH_RANGE_TOOLS = frozenset(
    {"word_check_text_style", "word_check_paragraph_format", "word_check_heading_numbering"}
)
# ``requirement_text`` accepts up to 50,000 Unicode characters at the API
# boundary.  A Chinese requirement can use roughly three UTF-8 bytes per
# character, so the function-call envelope must use a byte limit that is
# consistent with that public contract while still remaining bounded.
MAX_ARGUMENT_BYTES = 256 * 1024
MAX_RESULT_BYTES = 256 * 1024

# This is deliberately a closed registry.  Adding a tool requires a protocol
# review and a corresponding VSTO implementation.
VSTO_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "word_get_overview": {
        "type": "object",
        "properties": {
            "snapshot_id": {
                "type": ["string", "null"],
                "description": "已有快照 ID；首次调用为空",
            }
        },
        "additionalProperties": False,
    },
    "word_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
            "snapshot_id": {"type": ["string", "null"]},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "word_check_format": {
        "type": "object",
        "properties": {
            "requirements": {"type": "string", "minLength": 1, "maxLength": 50_000},
            "snapshot_id": {"type": ["string", "null"]},
        },
        "required": ["requirements"],
        "additionalProperties": False,
    },
    "word_scan_identity_clues": {
        "type": "object",
        "properties": {
            "categories": {
                "type": "array",
                "items": {"type": "string", "maxLength": 50},
                "maxItems": 20,
            },
            "snapshot_id": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    },
    # Deterministic checks are deliberately separate from the LLM-oriented
    # ``word_check_format`` sampler.  Each tool returns a bounded list of
    # violations plus an explicit coverage contract so the agent cannot turn
    # an unscanned document into a false ``pass``.
    "word_check_page_setup": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": ["string", "null"]},
            "check_a4": {"type": "boolean"},
            "check_margins": {"type": "boolean"},
            "check_white_background": {"type": "boolean"},
            "margin_cm": {"type": "number", "minimum": 0.1, "maximum": 20},
            "margin_top_cm": {"type": "number", "minimum": 0.1, "maximum": 20},
            "margin_bottom_cm": {"type": "number", "minimum": 0.1, "maximum": 20},
            "margin_left_cm": {"type": "number", "minimum": 0.1, "maximum": 20},
            "margin_right_cm": {"type": "number", "minimum": 0.1, "maximum": 20},
            "tolerance_pt": {"type": "number", "minimum": 0.01, "maximum": 10},
        },
        "additionalProperties": False,
    },
    "word_check_headers_footers": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": ["string", "null"]},
            "check_page_numbers": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "word_check_blank_pages": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": ["string", "null"]},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "additionalProperties": False,
    },
    "word_check_text_style": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": ["string", "null"]},
            "expected_font": {"type": "string", "maxLength": 100},
            "expected_font_far_east": {"type": "string", "maxLength": 100},
            "expected_size_pt": {"type": "number", "minimum": 1, "maximum": 200},
            "expected_color_rgb": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 255},
                "minItems": 3,
                "maxItems": 3,
            },
            "require_no_italic": {"type": "boolean"},
            "require_no_underline": {"type": "boolean"},
            "check_white_background": {"type": "boolean"},
            "max_characters": {"type": "integer", "minimum": 1000, "maximum": 1000000},
            "paragraph_start": {"type": "integer", "minimum": 1, "maximum": PARAGRAPH_RANGE_MAX},
            "paragraph_end": {"type": "integer", "minimum": 1, "maximum": PARAGRAPH_RANGE_MAX},
        },
        "additionalProperties": False,
    },
    "word_check_paragraph_format": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": ["string", "null"]},
            "check_line_spacing": {"type": "boolean"},
            "check_space": {"type": "boolean"},
            "line_spacing_rule": {"type": "string", "enum": ["exactly", "single", "multiple", "minimum", "any"]},
            "line_spacing_pt": {"type": "number", "minimum": 0, "maximum": 500},
            "line_spacing_multiple": {"type": "number", "minimum": 0, "maximum": 10},
            "space_before_pt": {"type": "number", "minimum": 0, "maximum": 500},
            "space_after_pt": {"type": "number", "minimum": 0, "maximum": 500},
            "paragraph_start": {"type": "integer", "minimum": 1, "maximum": PARAGRAPH_RANGE_MAX},
            "paragraph_end": {"type": "integer", "minimum": 1, "maximum": PARAGRAPH_RANGE_MAX},
        },
        "additionalProperties": False,
    },
    "word_check_heading_numbering": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": ["string", "null"]},
            "heading_policy": {
                "type": "string",
                "enum": ["require_heading_styles", "body_text_outline", "none"],
            },
            "check_number_format": {"type": "boolean"},
            "max_level": {"type": "integer", "minimum": 1, "maximum": 9},
            "formats": {
                "type": "array",
                "items": {"type": "string", "maxLength": 100},
                "maxItems": 9,
            },
            "review_point_markers": {
                "type": "array",
                "items": {"type": "string", "maxLength": 200},
                "maxItems": 50,
            },
            "paragraph_start": {"type": "integer", "minimum": 1, "maximum": PARAGRAPH_RANGE_MAX},
            "paragraph_end": {"type": "integer", "minimum": 1, "maximum": PARAGRAPH_RANGE_MAX},
        },
        "additionalProperties": False,
    },
    "word_check_objects": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": ["string", "null"]},
            "allow_images": {"type": "boolean"},
            "max_objects": {"type": "integer", "minimum": 1, "maximum": 300},
        },
        "additionalProperties": False,
    },
    "word_check_signatures": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    },
}

VSTO_TOOL_NAMES = frozenset(VSTO_TOOL_SCHEMAS)

# Protocol-test fallback only.  Production always uses Redis because the API
# and Celery workers are separate processes/hosts.
_LOCAL_RESULTS: dict[str, dict[str, Any]] = {}


def _redis_client():
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis

        return redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        logger.warning("Unable to create Redis client for VSTO broker: %s", exc)
        return None


def _result_key(call_id: str) -> str:
    return f"vsto:tool:result:{call_id}"


def publish_tool_result(call_id: str, payload: dict[str, Any], ttl: int = 300) -> None:
    """Publish a tool result for a waiting worker (idempotent overwrite)."""
    encoded = validate_tool_result_payload(payload)
    client = _redis_client()
    if client is not None:
        try:
            client.set(_result_key(call_id), encoded, ex=ttl)
            client.close()
            return
        except Exception as exc:
            logger.warning("Redis publish for VSTO call %s failed: %s", call_id, exc)
            try:
                client.close()
            except Exception:
                pass
    _LOCAL_RESULTS[call_id] = payload


def validate_tool_result_payload(payload: dict[str, Any]) -> str:
    """Validate and encode one result before it is committed or published."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("VSTO tool result must be valid JSON") from exc
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("VSTO tool result is too large")
    return encoded


def consume_tool_result(call_id: str) -> dict[str, Any] | None:
    """Read and remove a result.  Used by the Celery-side waiter."""
    client = _redis_client()
    if client is not None:
        try:
            encoded = client.getdel(_result_key(call_id))
            client.close()
            if encoded:
                return json.loads(encoded)
        except Exception as exc:
            logger.warning("Redis read for VSTO call %s failed: %s", call_id, exc)
            try:
                client.close()
            except Exception:
                pass
    return _LOCAL_RESULTS.pop(call_id, None)


def discard_tool_result(call_id: str) -> None:
    """Remove a late result when a VSTO session is closed."""
    client = _redis_client()
    if client is not None:
        try:
            client.delete(_result_key(call_id))
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            logger.warning("Redis cleanup for VSTO call %s failed: %s", call_id, exc)
        finally:
            try:
                client.close()
            except Exception:
                pass
    _LOCAL_RESULTS.pop(call_id, None)


# Progress heartbeats are published by the page on behalf of VSTO while a long
# scan is still running.  They are read (not consumed) by the waiting broker so
# repeated polls see the latest marker until the call settles.
_LOCAL_PROGRESS: dict[str, dict[str, Any]] = {}


def _progress_key(call_id: str) -> str:
    return f"vsto:tool:progress:{call_id}"


def publish_tool_progress(call_id: str, payload: dict[str, Any], ttl: int = 600) -> None:
    """Record the latest VSTO progress marker for a pending call."""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:4_000]
    client = _redis_client()
    if client is not None:
        try:
            client.set(_progress_key(call_id), encoded, ex=ttl)
            client.close()
            return
        except Exception as exc:
            logger.warning("Redis progress publish for VSTO call %s failed: %s", call_id, exc)
            try:
                client.close()
            except Exception:
                pass
    _LOCAL_PROGRESS[call_id] = dict(payload)


def read_tool_progress(call_id: str) -> dict[str, Any] | None:
    """Return the latest progress marker without consuming it."""
    client = _redis_client()
    if client is not None:
        try:
            encoded = client.get(_progress_key(call_id))
            client.close()
            if encoded:
                return json.loads(encoded)
        except Exception as exc:
            logger.warning("Redis progress read for VSTO call %s failed: %s", call_id, exc)
            try:
                client.close()
            except Exception:
                pass
    return _LOCAL_PROGRESS.get(call_id)


def discard_tool_progress(call_id: str) -> None:
    client = _redis_client()
    if client is not None:
        try:
            client.delete(_progress_key(call_id))
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            logger.warning("Redis progress cleanup for VSTO call %s failed: %s", call_id, exc)
        finally:
            try:
                client.close()
            except Exception:
                pass
    _LOCAL_PROGRESS.pop(call_id, None)


def _validate_paragraph_range(arguments: dict[str, Any]) -> None:
    start = arguments.get("paragraph_start")
    end = arguments.get("paragraph_end")
    for key, value in (("paragraph_start", start), ("paragraph_end", end)):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= PARAGRAPH_RANGE_MAX
        ):
            raise ValueError(f"{key} must be an integer between 1 and {PARAGRAPH_RANGE_MAX}")
    if start is not None and end is not None and start > end:
        raise ValueError("paragraph_start must not exceed paragraph_end")


def _validate_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> None:
    if tool_name not in VSTO_TOOL_NAMES:
        raise ValueError(f"VSTO tool is not allowed: {tool_name}")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("tool arguments must be valid JSON") from exc
    if len(encoded.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ValueError("VSTO tool arguments are too large")
    allowed = set(VSTO_TOOL_SCHEMAS[tool_name]["properties"])
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"unexpected tool arguments: {', '.join(sorted(unknown))}")
    snapshot_id = arguments.get("snapshot_id")
    if snapshot_id is not None and (
        not isinstance(snapshot_id, str) or not snapshot_id.strip() or len(snapshot_id) > 36
    ):
        raise ValueError("snapshot_id must be a non-empty string up to 36 characters")
    if tool_name in _PARAGRAPH_RANGE_TOOLS:
        _validate_paragraph_range(arguments)
    if tool_name == "word_search":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("word_search.query is required")
        if len(query) > 200:
            raise ValueError("word_search.query is too long")
        max_results = arguments.get("max_results")
        if max_results is not None and (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= 50
        ):
            raise ValueError("word_search.max_results must be an integer between 1 and 50")
    if tool_name == "word_check_format":
        requirements = arguments.get("requirements")
        if not isinstance(requirements, str) or not requirements.strip():
            raise ValueError("word_check_format.requirements is required")
        if len(requirements) > 50_000:
            raise ValueError("word_check_format.requirements is too long")
    if tool_name == "word_scan_identity_clues":
        categories = arguments.get("categories")
        if categories is not None:
            if not isinstance(categories, list) or len(categories) > 20:
                raise ValueError("word_scan_identity_clues.categories must contain at most 20 items")
            if any(not isinstance(item, str) or len(item) > 50 for item in categories):
                raise ValueError("identity clue categories must be strings up to 50 characters")
    if tool_name == "word_check_page_setup":
        for key in ("check_a4", "check_margins", "check_white_background"):
            if key in arguments and not isinstance(arguments[key], bool):
                raise ValueError(f"{key} must be a boolean")
        for key in (
            "margin_cm",
            "margin_top_cm",
            "margin_bottom_cm",
            "margin_left_cm",
            "margin_right_cm",
            "tolerance_pt",
        ):
            if key in arguments and isinstance(arguments[key], bool):
                raise ValueError(f"{key} must be a number")
    if tool_name == "word_check_headers_footers":
        if "check_page_numbers" in arguments and not isinstance(arguments["check_page_numbers"], bool):
            raise ValueError("check_page_numbers must be a boolean")
    if tool_name == "word_check_blank_pages":
        max_pages = arguments.get("max_pages")
        if max_pages is not None and (isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 1000):
            raise ValueError("max_pages must be an integer between 1 and 1000")
    if tool_name == "word_check_text_style":
        for key in ("expected_font", "expected_font_far_east"):
            if key in arguments and (not isinstance(arguments[key], str) or not arguments[key].strip()):
                raise ValueError(f"{key} must be a non-empty string")
        if "expected_color_rgb" in arguments:
            rgb = arguments["expected_color_rgb"]
            if not isinstance(rgb, list) or len(rgb) != 3 or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in rgb):
                raise ValueError("expected_color_rgb must contain exactly three integers between 0 and 255")
        for key in ("require_no_italic", "require_no_underline"):
            if key in arguments and not isinstance(arguments[key], bool):
                raise ValueError(f"{key} must be a boolean")
        if "check_white_background" in arguments and not isinstance(arguments["check_white_background"], bool):
            raise ValueError("check_white_background must be a boolean")
        max_characters = arguments.get("max_characters")
        if max_characters is not None and (isinstance(max_characters, bool) or not isinstance(max_characters, int) or not 1000 <= max_characters <= 1_000_000):
            raise ValueError("max_characters must be between 1000 and 1000000")
    if tool_name == "word_check_paragraph_format":
        for key in ("check_line_spacing", "check_space"):
            if key in arguments and not isinstance(arguments[key], bool):
                raise ValueError(f"{key} must be a boolean")
        for key in ("line_spacing_pt", "line_spacing_multiple", "space_before_pt", "space_after_pt"):
            if key in arguments and isinstance(arguments[key], bool):
                raise ValueError(f"{key} must be a number")
        rule = arguments.get("line_spacing_rule")
        if rule is not None and rule not in {"exactly", "single", "multiple", "minimum", "any"}:
            raise ValueError("unsupported line_spacing_rule")
    if tool_name == "word_check_heading_numbering":
        if "heading_policy" in arguments and arguments["heading_policy"] not in {
            "require_heading_styles",
            "body_text_outline",
            "none",
        }:
            raise ValueError("unsupported heading_policy")
        if "check_number_format" in arguments and not isinstance(arguments["check_number_format"], bool):
            raise ValueError("check_number_format must be a boolean")
        max_level = arguments.get("max_level")
        if max_level is not None and (isinstance(max_level, bool) or not isinstance(max_level, int) or not 1 <= max_level <= 9):
            raise ValueError("max_level must be an integer between 1 and 9")
        for key in ("formats", "review_point_markers"):
            values = arguments.get(key)
            if values is not None and (not isinstance(values, list) or len(values) > (9 if key == "formats" else 50) or any(not isinstance(v, str) for v in values)):
                raise ValueError(f"{key} must be an array of strings")
    if tool_name == "word_check_objects":
        if "allow_images" in arguments and not isinstance(arguments["allow_images"], bool):
            raise ValueError("allow_images must be a boolean")
        max_objects = arguments.get("max_objects")
        if max_objects is not None and (isinstance(max_objects, bool) or not isinstance(max_objects, int) or not 1 <= max_objects <= 300):
            raise ValueError("max_objects must be an integer between 1 and 300")


async def _emit(
    callback: Callable[[str, dict[str, Any]], Any] | None,
    event_type: str,
    data: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        result = callback(event_type, data)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception("VSTO broker event callback failed: %s", event_type)


def _memo_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """Identity of one call for the per-task result memo (snapshot is task-wide)."""
    stable = {key: value for key, value in arguments.items() if key != "snapshot_id"}
    return tool_name + "|" + json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class VstoToolBroker:
    """Create and await VSTO calls for one blind-check task."""

    def __init__(
        self,
        *,
        session_factory,
        task_id: str,
        tool_session_id: str,
        event_callback: Callable[[str, dict[str, Any]], Any] | None = None,
        timeout_seconds: int = TOOL_CALL_TIMEOUT_SECONDS,
        cancel_event: asyncio.Event | None = None,
        idle_timeout_seconds: int = TOOL_CALL_IDLE_TIMEOUT_SECONDS,
        wasted_budget_seconds: int = TOOL_CALL_WASTED_BUDGET_SECONDS,
    ) -> None:
        self.session_factory = session_factory
        self.task_id = task_id
        self.tool_session_id = tool_session_id
        self.event_callback = event_callback
        self.timeout_seconds = self._clamp_timeout(timeout_seconds)
        self.idle_timeout_seconds = max(5, int(idle_timeout_seconds))
        self.wasted_budget_seconds = max(0, int(wasted_budget_seconds))
        self.cancel_event = cancel_event
        # 同一任务内相同工具+相同参数的结果只向 Word 要一次（成功与失败都记）：
        # 模型看到“必要工具未成功”后原样再调，直接命中缓存而不是再排一次队。
        self._memo: dict[str, dict[str, Any]] = {}
        # 累计在超时上浪费的秒数；超过预算后熔断，不再发起新的 Word 调用。
        self.wasted_seconds = 0.0
        self.circuit_open = False

    @staticmethod
    def _clamp_timeout(value: Any) -> int:
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            seconds = TOOL_CALL_TIMEOUT_SECONDS
        return max(5, min(seconds, TOOL_CALL_MAX_TIMEOUT_SECONDS))

    def cached_result(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        entry = self._memo.get(_memo_key(tool_name, dict(arguments or {})))
        return json.loads(json.dumps(entry)) if entry is not None else None

    async def request(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
        max_wait_seconds: int | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Issue one function call and wait for the VSTO result.

        ``timeout_seconds`` is the initial wall-clock allowance; progress
        heartbeats from VSTO extend the deadline by ``idle_timeout_seconds`` at
        a time, never beyond ``max_wait_seconds`` (the DB/SSE ``expires_at``).
        """
        arguments = dict(arguments or {})
        _validate_tool_arguments(tool_name, arguments)
        memo_key = _memo_key(tool_name, arguments)
        if use_cache and memo_key in self._memo:
            cached = json.loads(json.dumps(self._memo[memo_key]))
            cached["cached"] = True
            if not cached.get("success"):
                cached["error"] = "（本次检查已用相同参数调用过该工具，未再次执行）" + str(cached.get("error") or "")
            await _emit(
                self.event_callback,
                "vsto_tool_cached",
                {"tool": tool_name, "success": bool(cached.get("success"))},
            )
            return cached
        if self.circuit_open or (
            self.wasted_budget_seconds and self.wasted_seconds >= self.wasted_budget_seconds
        ):
            self.circuit_open = True
            error = (
                "Word 工具累计无应答时间已超过上限，本次检查不再发起新的文档扫描；"
                "请确认插件是否仍在响应，或缩小文档后重新检查"
            )
            await _emit(
                self.event_callback,
                "vsto_tool_circuit_open",
                {"tool": tool_name, "wasted_seconds": int(self.wasted_seconds)},
            )
            return {"success": False, "data": {}, "content": "", "error": error, "circuit_open": True}

        timeout = self._clamp_timeout(timeout_seconds if timeout_seconds is not None else self.timeout_seconds)
        absolute = self._clamp_timeout(max_wait_seconds if max_wait_seconds is not None else timeout * 2)
        absolute = max(absolute, timeout)
        call_id = str(uuid.uuid4())
        requested_at = utc_now()
        expires_at = requested_at + timedelta(seconds=absolute)

        async with self.session_factory() as db:
            task = (
                await db.execute(select(BlindCheckTask).where(BlindCheckTask.id == self.task_id))
            ).scalar_one_or_none()
            session = (
                await db.execute(
                    select(VstoToolSession).where(VstoToolSession.id == self.tool_session_id)
                )
            ).scalar_one_or_none()
            if task is None or session is None:
                raise RuntimeError("blind-check task or VSTO session does not exist")
            if task.tool_session_id != session.id:
                raise RuntimeError("task is not bound to this VSTO session")
            if session.status != "active" or session.expires_at <= requested_at:
                raise RuntimeError("VSTO session has expired; reopen the Word panel")
            if task.status in {"cancelled", "failed", "completed"}:
                raise RuntimeError(f"task is no longer active: {task.status}")
            expected_snapshot_id = task.snapshot_id or session.snapshot_id
            if not expected_snapshot_id:
                raise RuntimeError("VSTO session is not bound to a Word document snapshot")
            supplied_snapshot_id = arguments.get("snapshot_id")
            if supplied_snapshot_id and supplied_snapshot_id != expected_snapshot_id:
                raise RuntimeError("tool requested a snapshot that is not bound to this task")
            if session.snapshot_id and session.snapshot_id != expected_snapshot_id:
                raise RuntimeError("VSTO session snapshot no longer matches this task")
            arguments["snapshot_id"] = expected_snapshot_id
            _validate_tool_arguments(tool_name, arguments)
            call = VstoToolCall(
                call_id=call_id,
                task_id=self.task_id,
                session_id=self.tool_session_id,
                tool_name=tool_name,
                arguments=arguments,
                status="pending",
                requested_at=requested_at,
                expires_at=expires_at,
            )
            db.add(call)
            await db.commit()

        await _emit(
            self.event_callback,
            "vsto_tool_request",
            {
                "call_id": call_id,
                "tool_session_id": self.tool_session_id,
                "tool": tool_name,
                "arguments": arguments,
                # requested_at 与 expires_at 成对下发：插件按二者之差（服务端 TTL）配合本机
                # 单调时钟判过期，不直接拿本机墙钟比 expires_at——用户机器时钟偏差不再误判。
                "requested_at": requested_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            },
        )

        started = time.monotonic()
        deadline = started + timeout
        absolute_deadline = started + absolute
        last_progress_marker: Any = None
        now = started
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if self.cancel_event is not None and self.cancel_event.is_set():
                error = "暗标检查已取消，已停止等待 Word 工具"
                await self._mark_failed(call_id, error)
                discard_tool_progress(call_id)
                await _emit(
                    self.event_callback,
                    "vsto_tool_cancelled",
                    {"call_id": call_id, "tool": tool_name, "message": error},
                )
                await self._emit_cancel(call_id, tool_name, "task_cancelled")
                return {"success": False, "data": {}, "content": "", "error": error}
            payload = await asyncio.to_thread(consume_tool_result, call_id)
            if payload is not None:
                discard_tool_progress(call_id)
                result = await self._finish_call(call_id, payload)
                self._memo[memo_key] = json.loads(json.dumps(result))
                return result
            # Polling the DB makes the broker work even when Redis is briefly
            # unavailable and lets cancellation/expiry be observed promptly.
            state = await self._get_call_state(call_id)
            if state and state[0] in {"completed", "failed", "expired"}:
                discard_tool_progress(call_id)
                if state[1] is not None:
                    self._memo[memo_key] = json.loads(json.dumps(state[1]))
                    return state[1]
                failure = {"success": False, "data": {}, "content": "", "error": state[2] or "VSTO tool call failed"}
                self._memo[memo_key] = dict(failure)
                return failure
            progress = await asyncio.to_thread(read_tool_progress, call_id)
            if progress:
                marker = (progress.get("seq"), progress.get("ts"), progress.get("checked_count"), progress.get("cursor"))
                if marker != last_progress_marker:
                    last_progress_marker = marker
                    # VSTO 仍在推进：把期限往后推一个空闲窗口，但绝不越过绝对上限。
                    deadline = min(absolute_deadline, max(deadline, now + self.idle_timeout_seconds))
                    await _emit(
                        self.event_callback,
                        "vsto_tool_progress",
                        {
                            "call_id": call_id,
                            "tool": tool_name,
                            "checked_count": progress.get("checked_count"),
                            "cursor": progress.get("cursor"),
                            "total": progress.get("total"),
                        },
                    )
            await asyncio.sleep(0.5)

        self.wasted_seconds += max(0.0, now - started)
        error = "VSTO 工具调用超时，文档可能已关闭或页面未连接"
        if last_progress_marker is not None:
            error = "VSTO 工具长时间没有新的进展，已停止等待（文档过大或插件无响应）"
        await self._mark_failed(call_id, error, status="expired")
        discard_tool_result(call_id)
        discard_tool_progress(call_id)
        await _emit(
            self.event_callback,
            "vsto_tool_timeout",
            {"call_id": call_id, "tool": tool_name, "message": error},
        )
        await self._emit_cancel(call_id, tool_name, "timeout")
        failure = {"success": False, "data": {}, "content": "", "error": error, "call_id": call_id, "expired": True}
        self._memo[memo_key] = {k: v for k, v in failure.items() if k != "call_id"}
        return failure

    async def _emit_cancel(self, call_id: str, tool_name: str, reason: str) -> None:
        """Ask the page to abort the still-running Word operation for this call."""
        await _emit(
            self.event_callback,
            "vsto_tool_cancel",
            {
                "call_id": call_id,
                "tool": tool_name,
                "tool_session_id": self.tool_session_id,
                "reason": reason,
            },
        )

    async def fetch_late_result(self, call_id: str) -> dict[str, Any] | None:
        """Return a result that arrived after the broker stopped waiting.

        Late results are accepted by the API while the task is still running
        and the snapshot is unchanged; a chunked caller checks here before
        paying for the same paragraph range again.
        """
        if not call_id:
            return None
        state = await self._get_call_state(call_id)
        if state and state[0] == "completed" and isinstance(state[1], dict) and state[1].get("success"):
            return state[1]
        return None

    async def _finish_call(self, call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        success = bool(payload.get("success"))
        expected_snapshot_id = await self._get_call_snapshot(call_id)
        returned_snapshot_id = payload.get("snapshot_id")
        snapshot_changed = bool(
            success
            and expected_snapshot_id
            and returned_snapshot_id != expected_snapshot_id
        )
        if snapshot_changed:
            success = False
        normalized = {
            "success": success,
            "data": payload.get("data") if success and isinstance(payload.get("data"), dict) else {},
            "content": str(payload.get("content") or "")[:200_000] if success else "",
            "error": (
                "Word 文档快照已变化，本次工具结果已作废，请重新检查"
                if snapshot_changed
                else str(payload.get("error") or "")[:2_000] if not success else None
            ),
            "snapshot_id": returned_snapshot_id,
        }
        await self._mark_finished(call_id, normalized, "completed" if success else "failed")
        await _emit(
            self.event_callback,
            "vsto_tool_result",
            {
                "call_id": call_id,
                "success": success,
                "tool_result": normalized if success else {"error": normalized["error"]},
            },
        )
        return normalized

    async def _get_call_state(self, call_id: str):
        async with self.session_factory() as db:
            row = (
                await db.execute(select(VstoToolCall).where(VstoToolCall.call_id == call_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            return row.status, row.result, row.error_message

    async def _get_call_snapshot(self, call_id: str) -> str | None:
        async with self.session_factory() as db:
            row = (
                await db.execute(select(VstoToolCall).where(VstoToolCall.call_id == call_id))
            ).scalar_one_or_none()
            if row is None or not isinstance(row.arguments, dict):
                return None
            value = row.arguments.get("snapshot_id")
            return value if isinstance(value, str) else None

    async def _mark_finished(self, call_id: str, result: dict[str, Any], status: str) -> None:
        async with self.session_factory() as db:
            row = (
                await db.execute(select(VstoToolCall).where(VstoToolCall.call_id == call_id))
            ).scalar_one_or_none()
            if row is None:
                return
            if row.status in {"completed", "failed", "expired"}:
                return
            row.status = status
            row.result = result
            row.error_message = result.get("error")
            row.answered_at = utc_now()
            await db.commit()

    async def _mark_failed(self, call_id: str, error: str, *, status: str = "failed") -> None:
        await self._mark_finished(
            call_id,
            {"success": False, "data": {}, "content": "", "error": error},
            status,
        )


def tool_schema(tool_name: str) -> dict[str, Any]:
    """Return a copy suitable for Mini-Agent/OpenAI function registration."""
    if tool_name not in VSTO_TOOL_SCHEMAS:
        raise KeyError(tool_name)
    return json.loads(json.dumps(VSTO_TOOL_SCHEMAS[tool_name]))
