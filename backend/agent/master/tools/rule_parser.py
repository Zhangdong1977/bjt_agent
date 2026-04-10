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

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "rule_doc_path": {"type": "string", "description": "Absolute path to rule document"}
            },
            "required": ["rule_doc_path"]
        }

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
        """解析 Markdown 格式的规则文档.

        文档结构：
        - # 文档标题
        - # 响应文件检查项定义
          - ## 检查项1 / ## 检查项2 / ... 作为检查项分隔
            - ### 检查项名称
            - ### 检查项规则描述
            - ### 正例
            - ### 反例
        """
        check_items = []
        current_item = None
        current_section = None
        section_lines = []

        lines = content.split("\n")
        in_check_items_section = False

        for line in lines:
            line_stripped = line.strip()

            # 检查是否进入"响应文件检查项定义"章节
            if re.match(r"^#\s+响应文件检查项定义\s*$", line_stripped):
                in_check_items_section = True
                continue

            # 如果不在检查项定义章节，跳过
            if not in_check_items_section:
                continue

            # 检查是否是新的检查项标题（## 检查项N）
            check_item_match = re.match(r"^##\s+检查项(\d+)\s*$", line_stripped)
            if check_item_match:
                # 保存之前的检查项
                if current_item and current_section is not None:
                    self._add_section_content(current_item, current_section, section_lines)
                    if current_item.get("check_item_name"):
                        check_items.append(current_item)

                # 创建新检查项
                current_item = {
                    "check_item_id": f"{doc_name}_{int(check_item_match.group(1)):03d}",
                    "check_item_name": "",
                    "check_item_rule_desc": "",
                    "positive_example": "",
                    "negative_example": "",
                }
                current_section = None
                section_lines = []
                continue

            # 检查是否是检查项的子章节（### 检查项名称 / ### 检查项规则描述 / ### 正例 / ### 反例）
            section_match = re.match(r"^###\s+(检查项名称|检查项规则描述|正例|反例)\s*$", line_stripped)
            if section_match and current_item is not None:
                # 保存之前的 section 内容
                if current_section:
                    self._add_section_content(current_item, current_section, section_lines)

                current_section = section_match.group(1)
                section_lines = []
                continue

            # 收集 section 内容
            if current_section is not None and current_item is not None:
                section_lines.append(line)

        # 保存最后一个检查项
        if current_item and current_section is not None:
            self._add_section_content(current_item, current_section, section_lines)
            if current_item.get("check_item_name"):
                check_items.append(current_item)

        return check_items

    def _add_section_content(self, current_item: dict, current_section: str, section_lines: list):
        """将 section 内容添加到检查项"""
        content = "\n".join(section_lines).strip()

        if current_section == "检查项名称":
            current_item["check_item_name"] = content
        elif current_section == "检查项规则描述":
            current_item["check_item_rule_desc"] = content
        elif current_section == "正例":
            current_item["positive_example"] = content
        elif current_section == "反例":
            current_item["negative_example"] = content


class RuleLibraryScannerTool(Tool):
    """扫描规则库目录，获取所有规则文档"""

    name = "rule_library_scanner"
    description = "扫描规则库目录，返回所有 .md 文件列表"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "rule_library_path": {"type": "string", "description": "Absolute path to rule library directory"}
            },
            "required": ["rule_library_path"]
        }

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