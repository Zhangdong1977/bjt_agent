"""用量归属上下文 — 用 contextvars 携带当次审查任务的归属信息。

LLM 写入点（bid_review_agent.wrapped_generate）与 OCR 写入点（baidu_ocr.execute）
都拿不到 todo_id / user 维度，通过本 ContextVar 由 SubAgentExecutor 在执行入口
设置、finally 重置。contextvars 在 asyncio 下天然按 task 隔离，并发子 agent 互不干扰。
"""

import contextvars
from dataclasses import dataclass
from typing import Optional


@dataclass
class UsageContext:
    """一次子 agent 执行期间固定的归属信息。"""
    external_user_id: Optional[int]      # sys_user.user_id
    local_user_id: Optional[str]         # 本地 users.id
    user_name: str
    enterprise_name: Optional[str]
    interior_user: bool
    project_id: Optional[str]
    task_id: Optional[str]               # ReviewTask.id（= 旧 session_id）
    todo_id: Optional[str]               # TodoItem.id


_current: contextvars.ContextVar[Optional[UsageContext]] = contextvars.ContextVar(
    "usage_context", default=None
)


def set_usage_context(ctx: UsageContext) -> contextvars.Token:
    """设置当前任务上下文，返回 token 供 reset 使用。"""
    return _current.set(ctx)


def reset_usage_context(token: contextvars.Token) -> None:
    """用 set 返回的 token 重置回上一级上下文。"""
    _current.reset(token)


def get_usage_context() -> Optional[UsageContext]:
    """获取当前上下文；无上下文（脚本/测试/异步任务外）返回 None。"""
    return _current.get()
