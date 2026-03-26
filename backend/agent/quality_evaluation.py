"""Quality Evaluation Module for Agent Output.

This module provides quality evaluation for the BidReviewAgent's output,
assessing compliance judgments, severity appropriateness, and completeness.

Evaluation Criteria:
- Compliance Accuracy: Whether is_compliant determination is correct
- Severity Appropriateness: Whether severity level matches the actual issue
- Completeness: Whether all required fields are populated

Uses LLM-as-Judge pattern for evaluation.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message

from backend.config import get_settings

settings = get_settings()

# Evaluation prompts
COMPLIANCE_EVALUATION_PROMPT = """You are a quality evaluator for a bid review agent.

Evaluate whether the agent's compliance determination is correct.

## Finding to Evaluate:
- Requirement: {requirement_content}
- Bid Content: {bid_content}
- Agent's Determination: is_compliant={is_compliant}
- Agent's Explanation: {explanation}

## Your Task:
Analyze whether the compliance determination is accurate based on the requirement and bid content.
Consider:
1. Does the bid content actually address the requirement?
2. Is the explanation supported by the bid content?
3. Are there any gaps or missing information that would change the determination?

## Output Format (JSON only):
{{
    "is_accurate": true/false,
    "score": 0-100,
    "explanation": "Brief explanation of your evaluation"
}}

Return ONLY valid JSON."""


SEVERITY_EVALUATION_PROMPT = """You are a quality evaluator for a bid review agent.

Evaluate whether the severity level assigned is appropriate.

## Finding to Evaluate:
- Requirement: {requirement_content}
- Bid Content: {bid_content}
- Compliance Status: is_compliant={is_compliant}
- Assigned Severity: {severity}
- Explanation: {explanation}

## Severity Definitions:
- critical: Missing mandatory documents, major qualification issues, or regulatory violations
- major: Technical specification deviations, commercial term mismatches, incomplete documentation
- minor: Format irregularities, unclear statements, optimization suggestions

## Your Task:
Determine if the assigned severity matches the actual severity of the issue.

## Output Format (JSON only):
{{
    "is_appropriate": true/false,
    "score": 0-100,
    "explanation": "Brief explanation of your evaluation"
}}

Return ONLY valid JSON."""


COMPLETENESS_EVALUATION_PROMPT = """You are a quality evaluator for a bid review agent.

Evaluate whether the finding has all necessary information.

## Finding to Evaluate:
- requirement_key: {requirement_key}
- requirement_content: {requirement_content}
- bid_content: {bid_content}
- is_compliant: {is_compliant}
- severity: {severity}
- location_page: {location_page}
- location_line: {location_line}
- suggestion: {suggestion}
- explanation: {explanation}

## Your Task:
Check if all required and optional fields are properly populated.
- Required fields: requirement_key, requirement_content, is_compliant, explanation
- Optional fields: bid_content, severity, location_page, location_line, suggestion

## Output Format (JSON only):
{{
    "is_complete": true/false,
    "score": 0-100,
    "missing_fields": ["field1", "field2"],
    "explanation": "Brief explanation of your evaluation"
}}

Return ONLY valid JSON."""


# Weights for overall quality score
COMPLIANCE_WEIGHT = 0.40
SEVERITY_WEIGHT = 0.30
COMPLETENESS_WEIGHT = 0.30

# Pass threshold
PASS_THRESHOLD = 70


@dataclass
class ComplianceAccuracyResult:
    """Result of compliance accuracy evaluation."""
    is_accurate: bool
    score: int  # 0-100
    explanation: str


@dataclass
class SeverityAppropriatenessResult:
    """Result of severity appropriateness evaluation."""
    is_appropriate: bool
    score: int  # 0-100
    explanation: str


@dataclass
class CompletenessResult:
    """Result of completeness evaluation."""
    is_complete: bool
    score: int  # 0-100
    missing_fields: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class FindingEvaluation:
    """Complete evaluation for a single finding."""
    requirement_key: str
    compliance_accuracy: ComplianceAccuracyResult
    severity_appropriateness: SeverityAppropriatenessResult
    completeness: CompletenessResult

    @property
    def overall_score(self) -> int:
        """Calculate weighted overall score for this finding."""
        return int(
            self.compliance_accuracy.score * COMPLIANCE_WEIGHT +
            self.severity_appropriateness.score * SEVERITY_WEIGHT +
            self.completeness.score * COMPLETENESS_WEIGHT
        )


@dataclass
class EvaluationResult:
    """Complete evaluation result for all findings."""
    total_findings: int
    overall_quality_score: int  # 0-100
    passed: bool
    individual_evaluations: list[FindingEvaluation]
    scores: dict[str, int] = field(default_factory=dict)


class QualityEvaluator:
    """LLM-as-Judge evaluator for agent output quality."""

    def __init__(self):
        """Initialize the quality evaluator."""
        self._llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )

    async def evaluate_compliance_accuracy(self, finding: dict) -> ComplianceAccuracyResult:
        """Evaluate whether the compliance determination is accurate.

        Args:
            finding: A single finding dict with requirement_content, bid_content,
                    is_compliant, and explanation

        Returns:
            ComplianceAccuracyResult with is_accurate, score, and explanation
        """
        try:
            prompt = COMPLIANCE_EVALUATION_PROMPT.format(
                requirement_content=finding.get("requirement_content", ""),
                bid_content=finding.get("bid_content", "N/A"),
                is_compliant=finding.get("is_compliant", True),
                explanation=finding.get("explanation", ""),
            )

            messages = [
                Message(
                    role="system",
                    content="You are a quality evaluator for bid review. Output ONLY valid JSON.",
                ),
                Message(role="user", content=prompt),
            ]

            response = await self._llm_client.generate(messages=messages)
            result = self._parse_json_response(response.content)

            return ComplianceAccuracyResult(
                is_accurate=result.get("is_accurate", False),
                score=min(100, max(0, result.get("score", 50))),
                explanation=result.get("explanation", ""),
            )

        except Exception as e:
            # Fallback evaluation on error
            return ComplianceAccuracyResult(
                is_accurate=False,
                score=0,
                explanation=f"Evaluation failed: {str(e)}",
            )

    async def evaluate_severity_appropriateness(self, finding: dict) -> SeverityAppropriatenessResult:
        """Evaluate whether the severity level is appropriate.

        Args:
            finding: A single finding dict with requirement_content, bid_content,
                    is_compliant, severity, and explanation

        Returns:
            SeverityAppropriatenessResult with is_appropriate, score, and explanation
        """
        try:
            prompt = SEVERITY_EVALUATION_PROMPT.format(
                requirement_content=finding.get("requirement_content", ""),
                bid_content=finding.get("bid_content", "N/A"),
                is_compliant=finding.get("is_compliant", True),
                severity=finding.get("severity") or "not applicable",
                explanation=finding.get("explanation", ""),
            )

            messages = [
                Message(
                    role="system",
                    content="You are a quality evaluator for bid review. Output ONLY valid JSON.",
                ),
                Message(role="user", content=prompt),
            ]

            response = await self._llm_client.generate(messages=messages)
            result = self._parse_json_response(response.content)

            return SeverityAppropriatenessResult(
                is_appropriate=result.get("is_appropriate", True),
                score=min(100, max(0, result.get("score", 50))),
                explanation=result.get("explanation", ""),
            )

        except Exception as e:
            # Fallback evaluation on error
            return SeverityAppropriatenessResult(
                is_appropriate=False,
                score=0,
                explanation=f"Evaluation failed: {str(e)}",
            )

    async def evaluate_completeness(self, finding: dict) -> CompletenessResult:
        """Evaluate whether all required fields are populated.

        Args:
            finding: A single finding dict

        Returns:
            CompletenessResult with is_complete, score, missing_fields, and explanation
        """
        try:
            prompt = COMPLETENESS_EVALUATION_PROMPT.format(
                requirement_key=finding.get("requirement_key", ""),
                requirement_content=finding.get("requirement_content", ""),
                bid_content=finding.get("bid_content") or "not provided",
                is_compliant=finding.get("is_compliant", True),
                severity=finding.get("severity") or "not applicable",
                location_page=finding.get("location_page"),
                location_line=finding.get("location_line"),
                suggestion=finding.get("suggestion") or "not provided",
                explanation=finding.get("explanation") or "not provided",
            )

            messages = [
                Message(
                    role="system",
                    content="You are a quality evaluator for bid review. Output ONLY valid JSON.",
                ),
                Message(role="user", content=prompt),
            ]

            response = await self._llm_client.generate(messages=messages)
            result = self._parse_json_response(response.content)

            return CompletenessResult(
                is_complete=result.get("is_complete", True),
                score=min(100, max(0, result.get("score", 50))),
                missing_fields=result.get("missing_fields", []),
                explanation=result.get("explanation", ""),
            )

        except Exception as e:
            # Fallback evaluation on error
            return CompletenessResult(
                is_complete=False,
                score=0,
                missing_fields=["evaluation failed"],
                explanation=f"Evaluation failed: {str(e)}",
            )

    async def evaluate_overall(self, finding: dict) -> FindingEvaluation:
        """Evaluate all quality criteria for a single finding.

        Args:
            finding: A single finding dict

        Returns:
            FindingEvaluation with all criterion evaluations and overall score
        """
        compliance_result = await self.evaluate_compliance_accuracy(finding)
        severity_result = await self.evaluate_severity_appropriateness(finding)
        completeness_result = await self.evaluate_completeness(finding)

        evaluation = FindingEvaluation(
            requirement_key=finding.get("requirement_key", "unknown"),
            compliance_accuracy=compliance_result,
            severity_appropriateness=severity_result,
            completeness=completeness_result,
        )

        return evaluation

    async def evaluate_findings_batch(self, findings: list[dict]) -> EvaluationResult:
        """Evaluate a batch of findings.

        Args:
            findings: List of finding dicts

        Returns:
            EvaluationResult with overall quality score
        """
        if not findings:
            return EvaluationResult(
                total_findings=0,
                overall_quality_score=0,
                passed=False,
                individual_evaluations=[],
                scores={},
            )

        individual_evaluations = []
        total_score = 0

        for finding in findings:
            evaluation = await self.evaluate_overall(finding)
            individual_evaluations.append(evaluation)
            total_score += evaluation.overall_score

        avg_score = total_score // len(findings) if findings else 0

        # Calculate score distribution
        scores = {
            "compliance_avg": sum(e.compliance_accuracy.score for e in individual_evaluations) // len(individual_evaluations),
            "severity_avg": sum(e.severity_appropriateness.score for e in individual_evaluations) // len(individual_evaluations),
            "completeness_avg": sum(e.completeness.score for e in individual_evaluations) // len(individual_evaluations),
        }

        return EvaluationResult(
            total_findings=len(findings),
            overall_quality_score=avg_score,
            passed=avg_score >= PASS_THRESHOLD,
            individual_evaluations=individual_evaluations,
            scores=scores,
        )

    def _parse_json_response(self, content: str) -> dict:
        """Parse JSON from LLM response with multiple fallback strategies.

        Args:
            content: Raw LLM response content

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

        # Last resort: return error indication
        return {
            "is_accurate": False,
            "score": 0,
            "explanation": f"Failed to parse LLM response: {content[:200]}",
        }
