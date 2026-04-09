# backend/agent/master/tools/rule_parser.py
import re
import json
from pathlib import Path
from typing import Optional
from mini_agent.tools.base import Tool, ToolResult


class RuleParserTool(Tool):
    """解析规则文档，提取检查项"""

    name = "rule_parser"
    description = "解析规则文档，提取检查项列表"

    async def execute(self, rule_doc_path: str) -> ToolResult:
        """
        解析单个规则文档，提取检查项

        Args:
            rule_doc_path: 规则文档的绝对路径

        Returns:
            ToolResult with check_items list
        """
        try:
            path = Path(rule_doc_path)
            if not path.exists():
                return ToolResult(
                    success=False,
                    content="",
                    error=f"Rule doc not found: {rule_doc_path}",
                )

            content = path.read_text(encoding="utf-8")
            check_items = self._parse_markdown(content, path.stem)

            return ToolResult(
                success=True,
                content=json.dumps({
                    "success": True,
                    "check_items": check_items,
                    "total_count": len(check_items),
                }),
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))

    def _parse_markdown(self, content: str, doc_name: str) -> list[dict]:
        """解析 Markdown 格式的规则文档"""
        check_items = []
        current_item = None
        current_lines = []

        lines = content.split("\n")
        for line in lines:
            line = line.strip()

            # 检查是否是新检查项的标题（## 开头的标题）
            header_match = re.match(r"^##\s+(.+)$", line)
            if header_match:
                # 保存之前的检查项
                if current_item:
                    current_item["rule_content"] = "\n".join(current_lines).strip()
                    check_items.append(current_item)

                current_item = {
                    "check_item_id": f"{doc_name}_{len(check_items) + 1:03d}",
                    "title": header_match.group(1).strip(),
                    "description": "",
                    "rule_content": "",
                }
                current_lines = []
                continue

            # 如果有当前检查项，收集内容
            if current_item is not None:
                current_lines.append(line)

        # 保存最后一个检查项
        if current_item:
            current_item["rule_content"] = "\n".join(current_lines).strip()
            check_items.append(current_item)

        return check_items


class RuleLibraryScannerTool(Tool):
    """扫描规则库目录，获取所有规则文档"""

    name = "rule_library_scanner"
    description = "扫描规则库目录，返回所有 .md 文件列表"

    async def execute(self, rule_library_path: str) -> ToolResult:
        """扫描规则库目录"""
        try:
            path = Path(rule_library_path)
            if not path.exists() or not path.is_dir():
                return ToolResult(
                    success=False,
                    content="",
                    error=f"Rule library not found: {rule_library_path}",
                )

            md_files = list(path.glob("*.md"))
            rule_docs = [
                {
                    "path": str(f.absolute()),
                    "name": f.name,
                    "stem": f.stem,
                }
                for f in sorted(md_files)
            ]

            return ToolResult(
                success=True,
                content=json.dumps({
                    "success": True,
                    "rule_docs": rule_docs,
                    "total_count": len(rule_docs),
                }),
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))