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
    # 本次执行是否需要印章识别（BaiduOcrTool 据此门控 /ocr/v1/seal 调用）。
    # 由 SubAgentExecutor 按子 agent 的规则文档判定：仅 E001 等印章相关规则
    # 置 True；解析素材图（印章小节写进 parsed.md 供 E001 消费）也置 True。
    # 默认 False —— 未表态的执行流（查重/报告/编标等）不发印章请求。
    seal_required: bool = False


# 需要印章识别的审查规则（规则文档编号 = 文件名第一个空格前的部分，
# 与 api/review.py 的 DEFAULT_UNSELECTED_RULE_DOC_CODES 同口径）。
# E001 签字盖章检查默认不勾选，此前未勾选的任务每图白烧印章配额。
SEAL_REQUIRED_RULE_CODES = frozenset({"E001"})


def rule_doc_code(rule_doc_path: Optional[str]) -> Optional[str]:
    """规则文档编号：'.../E001 签字盖章检查.md' → 'E001'。"""
    if not rule_doc_path:
        return None
    name = rule_doc_path.replace("\\", "/").rsplit("/", 1)[-1]
    code = name.split(" ", 1)[0].strip()
    return code or None


def seal_required_for_rule_doc(rule_doc_path: Optional[str]) -> bool:
    """规则文档是否需要印章识别；空路径/解析不出编号 → False（无明确需求不开）。"""
    return rule_doc_code(rule_doc_path) in SEAL_REQUIRED_RULE_CODES


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
