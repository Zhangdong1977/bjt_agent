"""SubAgentExecutor - 子代理执行器 - 管理单个子代理的生命周期."""

import asyncio
from datetime import datetime
from typing import Optional, Callable
from backend.models.todo_item import TodoItem
from backend.agent.bid_review_agent import BidReviewAgent


class SubAgentExecutor:
    """子代理执行器 - 管理单个子代理的生命周期."""

    def __init__(
        self,
        todo_item: TodoItem,
        tender_doc_path: str,
        bid_doc_path: str,
        user_id: str,
        event_callback: Optional[Callable] = None,
    ):
        self.todo_item = todo_item
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        self.user_id = user_id
        self.event_callback = event_callback
        self._agent: Optional[BidReviewAgent] = None

    def _send_event(self, event_type: str, data: dict):
        """发送 SSE 事件."""
        if self.event_callback:
            self.event_callback(event_type, data)

    async def create_agent(self, max_steps: int = 100) -> BidReviewAgent:
        """创建子代理实例.

        Args:
            max_steps: Maximum number of agent steps to run.
        """
        agent = BidReviewAgent(
            project_id=self.todo_item.project_id,
            tender_doc_path=self.tender_doc_path,
            bid_doc_path=self.bid_doc_path,
            user_id=self.user_id,
            event_callback=self.event_callback,
            max_steps=max_steps,
        )
        await agent.initialize()
        self._agent = agent
        return agent

    async def execute(self, max_steps: int = 50) -> dict:
        """执行子代理检查任务."""
        try:
            # 发送开始事件
            self._send_event("sub_agent_started", {
                "todo_id": self.todo_item.id,
                "rule_doc_name": self.todo_item.rule_doc_name,
            })

            # 创建 agent
            agent = await self.create_agent(max_steps=max_steps)

            # 构建任务描述
            check_items_text = self._build_check_items_text()
            task = f"""请根据以下规则检查投标文件：

规则文档：{self.todo_item.rule_doc_name}
检查项列表：
{check_items_text}

请按顺序执行每个检查项，输出结构化的检查结果。
最终输出必须包含一个JSON数组，每项代表一个审查发现。"""

            agent.add_user_message(task)

            # 执行检查
            findings = await agent.run_review()

            return {
                "success": True,
                "findings": findings,
                "todo_id": self.todo_item.id,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "todo_id": self.todo_item.id,
            }
        finally:
            if self._agent:
                await self._agent.close()

    def _build_check_items_text(self) -> str:
        """构建检查项文本."""
        lines = []
        for i, item in enumerate(self.todo_item.check_items, 1):
            title = item.get("title", "未命名检查项")
            description = item.get("description", "")
            rule_content = item.get("rule_content", "")
            lines.append(f"{i}. {title}")
            if description:
                lines.append(f"   描述: {description}")
            if rule_content:
                lines.append(f"   规则: {rule_content[:200]}...")
        return "\n".join(lines)

    async def close(self):
        """关闭子代理."""
        if self._agent:
            await self._agent.close()
            self._agent = None


def detect_anomaly(result: dict, todo_item: TodoItem) -> bool:
    """检测结果是否异常."""
    if not result:
        return True

    if not result.get("success", False):
        return True

    findings = result.get("findings", [])
    if not findings:
        return True

    # 检查是否为全合规但检查项数量不对
    all_compliant = all(f.get("is_compliant", False) for f in findings)
    if all_compliant and len(findings) < len(todo_item.check_items):
        return True

    return False