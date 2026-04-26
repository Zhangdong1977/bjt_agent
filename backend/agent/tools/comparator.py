"""比对工具，用于将应标书内容与招标书要求进行比对。"""

import json
import re
from typing import Optional

from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message
from backend.agent.tools.base import ToolResult
from mini_agent.tools.base import Tool as BaseTool

from backend.config import get_settings

settings = get_settings()

# 比对提示词 - 增强版，支持位置提取
COMPARISON_PROMPT = """你是一位专业的招投标合规分析师。

请将以下招标要求与应标书内容进行比对，判断是否满足要求。

## 招标要求：
{requirement}

## 应标书内容：
{bid_content}

## 严重程度定义（必须严格遵循）：
- **critical（严重）**：缺失必须文件、主要资质不符合、法规强制要求未满足
  - 示例：缺失必备文件、关键资质证书未取得、法律要求未满足
- **major（主要）**：技术指标偏差、商务条款不符、文档不完整
  - 示例：技术规格偏离、商务条款不匹配、文档不完整
- **minor（次要）**：格式不规范、表述不清晰、优化建议
  - 示例：格式问题、表述不清晰、优化建议

## 可选要求的特殊处理：
- 如果招标文件中使用"可"（可以）、"可选"（optional）、"可给予补充说明"等表述，说明该要求为可选
- 对于可选要求：如果应标书未提供，评级为**次要**
- 但如果可选要求引用了强制性标准（如"可补充说明行业/强制性标准"），则根据相关标准的严重程度评级
- 如果招标文件中使用"必须"（must）、"应当"（shall）、"以...为准"（shall prevail）或"强制"（mandatory）等表述，则为强制性要求

## 你的任务：
分析应标书内容是否满足招标要求。请考虑：
1. 应标书是否明确回应了该要求？
2. 回应是否完整详细？
3. 是否存在遗漏或缺失信息？
4. 根据招标文件措辞，这是强制性要求还是可选要求？

## 输出格式（JSON）：
{{
    "is_compliant": true/false,
    "severity": "critical/major/minor"（仅在不满足时填写）,
    "explanation": "简要说明你的分析（1-3句话）",
    "suggestion": "如果不满足要求，提供具体可操作的改进建议",
    "location_page": null 或整数（应标文档中找到相关内容的页码）,
    "location_line": null 或整数（应标文档中找到相关内容的行号）
}}

注意事项：
- 如果要求已满足，设置 is_compliant=true，不填写 severity
- 如果要求未满足，设置 is_compliant=false，并根据上述定义指定 severity
- 如果不确定，默认严重程度为"major"，但必须遵循上述定义
- location_page/location_line 为可选字段，但有助于在原文档中定位问题
- 分析要精确、彻底"""


class ComparatorTool(BaseTool):
    """使用LLM将应标书内容与招标要求进行比对的工具。"""

    def __init__(self):
        """初始化比对工具。"""
        super().__init__()
        # 初始化 MiniMax 的 LLM 客户端
        self._llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,  # MiniMax 使用 OpenAI 协议
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )

    @property
    def name(self) -> str:
        return "compare_bid"

    @property
    def description(self) -> str:
        return """将应标书内容与招标要求进行比对。
输入应为JSON对象，包含：
- 'requirement': 招标要求文本
- 'bid_content': 要比对的应标书内容（可包含行号，如"Line 5: content"）
- 'severity': 不满足时的默认严重程度（'critical'、'major'、'minor'），默认为'major'

返回结构化的比对结果，包含合规状态、严重程度、说明和位置信息。"""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "requirement": {
                    "type": "string",
                    "description": "要比对的招标要求",
                },
                "bid_content": {
                    "type": "string",
                    "description": "相关的应标书内容（可包含行号提示）",
                },
                "severity": {
                    "type": "string",
                    "enum": ["critical", "major", "minor"],
                    "description": "不满足时的严重程度",
                    "default": "major",
                },
            },
            "required": ["requirement", "bid_content"],
        }

    def _extract_location_from_content(self, bid_content: str) -> tuple[Optional[int], Optional[int]]:
        """从应标书内容中提取页码和行号。

        查找以下模式：
        - "Line 23: content"
        - "Page 5, Line 23: content"
        - 任意位置的 "line 23"

        返回 (page, line) 元组。
        """
        page_match = re.search(r'Page\s*(\d+)', bid_content, re.IGNORECASE)
        line_match = re.search(r'Line\s*(\d+)', bid_content, re.IGNORECASE)

        page = int(page_match.group(1)) if page_match else None
        line = int(line_match.group(1)) if line_match else None

        return page, line

    async def execute(
        self,
        requirement: str,
        bid_content: str,
        severity: str = "major",
    ) -> ToolResult:
        """使用LLM执行比对。

        Args:
            requirement: 招标要求
            bid_content: 要比对的应标书内容
            severity: 默认严重程度

        Returns:
            包含比对结果的 ToolResult
        """
        try:
            # 处理前从 bid_content 中提取位置提示
            hint_page, hint_line = self._extract_location_from_content(bid_content)

            # 如果应标书内容为空，自动判定为不合规
            if not bid_content or bid_content == "N/A":
                result = {
                    "is_compliant": False,
                    "severity": "critical",
                    "explanation": "未提供该要求的应标书内容。",
                    "suggestion": "请提供针对该要求的应标书相关内容。",
                    "location_page": None,
                    "location_line": None,
                }
            else:
                # 使用 LLM 进行比对
                prompt = COMPARISON_PROMPT.format(
                    requirement=requirement,
                    bid_content=bid_content,
                )

                messages = [
                    Message(
                        role="system",
                        content="你是一位专业的招投标合规分析师。请只输出包含所有必需字段的有效JSON。",
                    ),
                    Message(role="user", content=prompt),
                ]

                response = await self._llm_client.generate(messages=messages)

                # 解析 LLM 响应
                result = self._parse_json_response(response.content, severity)

            # 确保结果包含必需字段
            if "is_compliant" not in result:
                result["is_compliant"] = False
            if not result["is_compliant"] and "severity" not in result:
                result["severity"] = severity

            # 如果 LLM 未提供位置，使用提示位置
            if result.get("location_page") is None and hint_page is not None:
                result["location_page"] = hint_page
            if result.get("location_line") is None and hint_line is not None:
                result["location_line"] = hint_line

            # 格式化为符合 ReviewResult 模型的结果
            formatted_result = {
                "requirement": requirement,
                "bid_content": bid_content if bid_content else "N/A",
                "is_compliant": result.get("is_compliant", False),
                "severity": result.get("severity") if not result.get("is_compliant", True) else None,
                "explanation": result.get("explanation", ""),
                "suggestion": result.get("suggestion", ""),
                "location_page": result.get("location_page"),
                "location_line": result.get("location_line"),
            }

            # 生成人类可读的内容摘要
            if formatted_result["is_compliant"]:
                friendly_content = f"✅ 满足要求"
            else:
                severity_text = {
                    "critical": "严重",
                    "major": "较大",
                    "minor": "一般",
                }.get(formatted_result["severity"] or "major", "一般")
                friendly_content = f"❌ 不满足要求（严重程度：{severity_text}）"
                if formatted_result["explanation"]:
                    friendly_content += f"\n📝 {formatted_result['explanation']}"
                if formatted_result["suggestion"]:
                    friendly_content += f"\n💡 建议：{formatted_result['suggestion']}"

            return ToolResult(
                success=True,
                content=friendly_content,
                data=formatted_result,
            )

        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))

    def _parse_json_response(self, content: str, default_severity: str) -> dict:
        """使用多种备用策略解析 LLM 响应中的 JSON。

        Args:
            content: 原始 LLM 响应内容
            default_severity: 如果未提供严重程度，则使用此默认值

        Returns:
            解析后的结果字典
        """
        # 首先尝试直接解析 JSON
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取 JSON
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试在内容中查找 JSON 对象
        json_match = re.search(r'\{[^{}]*"[^}]+\}[^{}]*\}', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # 最后手段：尝试手动提取关键值
        return {
            "is_compliant": False,
            "severity": default_severity,
            "explanation": f"无法解析 LLM 响应：{content[:200]}",
            "suggestion": "请人工审核应标书内容。",
            "location_page": None,
            "location_line": None,
        }
