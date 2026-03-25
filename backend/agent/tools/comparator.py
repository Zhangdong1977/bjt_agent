"""Comparator tool for comparing bid content against tender requirements."""

import json

from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message
from mini_agent.tools.base import Tool as BaseTool, ToolResult

from backend.config import get_settings

settings = get_settings()

# Comparison prompt for LLM
COMPARISON_PROMPT = """You are a professional tender/bid compliance analyst.

Compare the following tender requirement against the bid document content and determine compliance.

## Tender Requirement:
{requirement}

## Bid Document Content:
{bid_content}

## Your Task:
Analyze whether the bid content satisfies the tender requirement. Consider:
1. Does the bid explicitly address the requirement?
2. Is the response complete and detailed?
3. Are there any gaps or missing information?

## Output Format (JSON):
{{
    "is_compliant": true/false,
    "severity": "critical/major/minor" (only if non-compliant),
    "explanation": "Brief explanation of your analysis",
    "suggestion": "Specific suggestion for improvement if non-compliant"
}}

Be precise and thorough in your analysis."""


class ComparatorTool(BaseTool):
    """Tool for comparing bid content against tender requirements using LLM."""

    def __init__(self):
        """Initialize the comparator tool."""
        super().__init__()
        # Initialize LLM client for MiniMax
        self._llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,  # MiniMax uses OpenAI protocol
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )

    @property
    def name(self) -> str:
        return "compare_bid"

    @property
    def description(self) -> str:
        return "Compare bid document content against a specific tender requirement. Input should be a JSON object with 'requirement', 'bid_content', and optional 'severity' (default 'major')."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "requirement": {
                    "type": "string",
                    "description": "The tender requirement to compare against",
                },
                "bid_content": {
                    "type": "string",
                    "description": "The relevant bid document content",
                },
                "severity": {
                    "type": "string",
                    "enum": ["critical", "major", "minor"],
                    "description": "Severity level if non-compliant",
                    "default": "major",
                },
            },
            "required": ["requirement", "bid_content"],
        }

    async def execute(
        self,
        requirement: str,
        bid_content: str,
        severity: str = "major",
    ) -> ToolResult:
        """Execute the comparison using LLM.

        Args:
            requirement: The tender requirement
            bid_content: The bid document content to compare
            severity: Default severity level

        Returns:
            ToolResult with comparison result
        """
        try:
            # If bid content is empty, automatically non-compliant
            if not bid_content or bid_content == "N/A":
                result = {
                    "is_compliant": False,
                    "severity": "critical",
                    "explanation": "No bid content provided for this requirement.",
                    "suggestion": "Please provide relevant bid content addressing this requirement.",
                }
            else:
                # Use LLM for comparison
                prompt = COMPARISON_PROMPT.format(
                    requirement=requirement,
                    bid_content=bid_content,
                )

                messages = [
                    Message(role="system", content="You are a professional tender/bid compliance analyst. Output ONLY valid JSON."),
                    Message(role="user", content=prompt),
                ]

                response = await self._llm_client.generate(messages=messages)

                # Parse LLM response
                try:
                    # Try to extract JSON from response
                    content = response.content.strip()
                    # Remove markdown code blocks if present
                    if content.startswith("```"):
                        lines = content.split("\n")
                        content = "\n".join(lines[1:-1])
                    result = json.loads(content)
                except json.JSONDecodeError:
                    # Fallback if JSON parsing fails
                    result = {
                        "is_compliant": False,
                        "severity": severity,
                        "explanation": f"LLM response parsing failed: {response.content[:200]}",
                        "suggestion": "Please review the bid content manually.",
                    }

            # Ensure result has required fields
            if "is_compliant" not in result:
                result["is_compliant"] = False
            if not result["is_compliant"] and "severity" not in result:
                result["severity"] = severity

            # Format output
            formatted_result = {
                "requirement": requirement,
                "bid_content": bid_content if bid_content else "N/A",
                "is_compliant": result.get("is_compliant", False),
                "severity": result.get("severity") if not result.get("is_compliant", True) else None,
                "explanation": result.get("explanation", ""),
                "suggestion": result.get("suggestion", ""),
            }

            return ToolResult(
                success=True,
                content=json.dumps(formatted_result, ensure_ascii=False, indent=2),
                data=formatted_result,
            )

        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))
