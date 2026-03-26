"""Comparator tool for comparing bid content against tender requirements."""

import json
import re
from typing import Optional

from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message
from mini_agent.tools.base import Tool as BaseTool, ToolResult

from backend.config import get_settings

settings = get_settings()

# Comparison prompt for LLM - enhanced with location extraction
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
    "severity": "critical/major/minor" (only if non-compliant, default to "major"),
    "explanation": "Brief explanation of your analysis (1-3 sentences)",
    "suggestion": "Specific, actionable suggestion for improvement if non-compliant",
    "location_page": null or integer (page number in bid document where relevant content was found),
    "location_line": null or integer (line number in bid document where relevant content was found)
}}

Notes:
- If the requirement is met, set is_compliant=true and omit severity
- If the requirement is NOT met, set is_compliant=false and specify severity
- location_page/location_line are optional but help in locating findings in the original document
- Be precise and thorough in your analysis"""


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
        return """Compare bid document content against a specific tender requirement.
Input should be a JSON object with:
- 'requirement': The tender requirement text
- 'bid_content': The bid document content to compare (can include line numbers like "Line 5: content")
- 'severity': Default severity if non-compliant ('critical', 'major', 'minor'), defaults to 'major'

Returns structured comparison result with compliance status, severity, explanation, and location."""

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
                    "description": "The relevant bid document content (can include line number hints)",
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

    def _extract_location_from_content(self, bid_content: str) -> tuple[Optional[int], Optional[int]]:
        """Extract page and line numbers from bid content if present.

        Looks for patterns like:
        - "Line 23: content"
        - "Page 5, Line 23: content"
        - "line 23" anywhere in content

        Returns (page, line) tuple.
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
        """Execute the comparison using LLM.

        Args:
            requirement: The tender requirement
            bid_content: The bid document content to compare
            severity: Default severity level

        Returns:
            ToolResult with comparison result
        """
        try:
            # Extract location hints from bid_content before processing
            hint_page, hint_line = self._extract_location_from_content(bid_content)

            # If bid content is empty, automatically non-compliant
            if not bid_content or bid_content == "N/A":
                result = {
                    "is_compliant": False,
                    "severity": "critical",
                    "explanation": "No bid content provided for this requirement.",
                    "suggestion": "Please provide relevant bid content addressing this requirement.",
                    "location_page": None,
                    "location_line": None,
                }
            else:
                # Use LLM for comparison
                prompt = COMPARISON_PROMPT.format(
                    requirement=requirement,
                    bid_content=bid_content,
                )

                messages = [
                    Message(
                        role="system",
                        content="You are a professional tender/bid compliance analyst. Output ONLY valid JSON with all required fields.",
                    ),
                    Message(role="user", content=prompt),
                ]

                response = await self._llm_client.generate(messages=messages)

                # Parse LLM response
                result = self._parse_json_response(response.content, severity)

            # Ensure result has required fields
            if "is_compliant" not in result:
                result["is_compliant"] = False
            if not result["is_compliant"] and "severity" not in result:
                result["severity"] = severity

            # Use hint location if LLM didn't provide one
            if result.get("location_page") is None and hint_page is not None:
                result["location_page"] = hint_page
            if result.get("location_line") is None and hint_line is not None:
                result["location_line"] = hint_line

            # Format output matching ReviewResult model
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

            return ToolResult(
                success=True,
                content=json.dumps(formatted_result, ensure_ascii=False, indent=2),
                data=formatted_result,
            )

        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))

    def _parse_json_response(self, content: str, default_severity: str) -> dict:
        """Parse JSON from LLM response with multiple fallback strategies.

        Args:
            content: Raw LLM response content
            default_severity: Default severity if not provided

        Returns:
            Parsed result dict
        """
        # Try direct JSON parse first
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object in the content
        json_match = re.search(r'\{[^{}]*"[^}]+\}[^{}]*\}', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Last resort: try to extract key values manually
        return {
            "is_compliant": False,
            "severity": default_severity,
            "explanation": f"Failed to parse LLM response: {content[:200]}",
            "suggestion": "Please review the bid content manually.",
            "location_page": None,
            "location_line": None,
        }
