"""Tests for Agent Quality Evaluation mechanism.

Test cases:
- EVAL-001: Evaluate compliance accuracy - correct compliant finding
- EVAL-002: Evaluate compliance accuracy - correct non-compliant finding
- EVAL-003: Evaluate compliance accuracy - incorrect compliance determination
- EVAL-004: Evaluate severity appropriateness - correct severity
- EVAL-005: Evaluate severity appropriateness - incorrect severity
- EVAL-006: Evaluate completeness - all fields populated
- EVAL-007: Evaluate completeness - missing optional fields
- EVAL-008: Overall quality score calculation
- EVAL-009: Evaluate empty findings list
- EVAL-010: Quality evaluation with multiple findings
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.agent.quality_evaluation import (
    QualityEvaluator,
    EvaluationResult,
    ComplianceAccuracyResult,
    SeverityAppropriatenessResult,
    CompletenessResult,
    FindingEvaluation,
)


class TestComplianceAccuracy:
    """Tests for compliance accuracy evaluation."""

    @pytest.fixture
    def evaluator(self):
        """Create evaluator with mocked LLM."""
        with patch("backend.agent.quality_evaluation.LLMClient") as mock_llm:
            mock_instance = MagicMock()
            mock_llm.return_value = mock_instance
            eval = QualityEvaluator()
            eval._llm_client = mock_instance
            return eval

    @pytest.mark.asyncio
    async def test_correct_compliant_determination(self, evaluator):
        """EVAL-001: Correct compliant finding should score high."""
        mock_response = MagicMock()
        mock_response.content = '{"is_accurate": true, "score": 95, "explanation": "Correct determination"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        finding = {
            "requirement_key": "req_001",
            "requirement_content": "投标人须具备ISO9001认证",
            "bid_content": "已取得ISO9001认证，证书编号：XXXXXX",
            "is_compliant": True,
            "severity": None,
            "explanation": "投标文件明确提供了ISO9001认证信息",
        }

        result = await evaluator.evaluate_compliance_accuracy(finding)

        assert result.is_accurate is True
        assert result.score >= 70
        assert "correct" in result.explanation.lower() or "accurate" in result.explanation.lower() or result.score >= 70

    @pytest.mark.asyncio
    async def test_correct_non_compliant_determination(self, evaluator):
        """EVAL-002: Correct non-compliant finding should score high."""
        mock_response = MagicMock()
        mock_response.content = '{"is_accurate": true, "score": 90, "explanation": "Correct non-compliant determination"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        finding = {
            "requirement_key": "req_002",
            "requirement_content": "交货期须在合同签订后30天内完成",
            "bid_content": "交货期：合同签订后45天内",
            "is_compliant": False,
            "severity": "major",
            "explanation": "交货期超过要求7天",
        }

        result = await evaluator.evaluate_compliance_accuracy(finding)

        assert result.is_accurate is True
        assert result.score >= 70

    @pytest.mark.asyncio
    async def test_incorrect_compliance_determination(self, evaluator):
        """EVAL-003: Incorrect compliance determination should score low."""
        mock_response = MagicMock()
        mock_response.content = '{"is_accurate": false, "score": 30, "explanation": "Agent claimed compliant but bid content does not address requirement"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        finding = {
            "requirement_key": "req_003",
            "requirement_content": "需要提供三年财务报告",
            "bid_content": "N/A",
            "is_compliant": True,
            "severity": None,
            "explanation": "财务信息已提供",
        }

        result = await evaluator.evaluate_compliance_accuracy(finding)

        assert result.is_accurate is False
        assert result.score < 70


class TestSeverityAppropriateness:
    """Tests for severity appropriateness evaluation."""

    @pytest.fixture
    def evaluator(self):
        """Create evaluator with mocked LLM."""
        with patch("backend.agent.quality_evaluation.LLMClient") as mock_llm:
            mock_instance = MagicMock()
            mock_llm.return_value = mock_instance
            eval = QualityEvaluator()
            eval._llm_client = mock_instance
            return eval

    @pytest.mark.asyncio
    async def test_appropriate_critical_severity(self, evaluator):
        """EVAL-004: Critical severity for truly critical issue scores high."""
        mock_response = MagicMock()
        mock_response.content = '{"is_appropriate": true, "score": 95, "explanation": "Missing mandatory certification is critical"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        finding = {
            "requirement_key": "req_001",
            "requirement_content": "投标人须具备ISO9001认证",
            "bid_content": "N/A",
            "is_compliant": False,
            "severity": "critical",
            "explanation": "缺少必须的质量管理体系认证",
        }

        result = await evaluator.evaluate_severity_appropriateness(finding)

        assert result.is_appropriate is True
        assert result.score >= 70

    @pytest.mark.asyncio
    async def test_inappropriate_severity_level(self, evaluator):
        """EVAL-005: Minor issue marked as critical scores low."""
        mock_response = MagicMock()
        mock_response.content = '{"is_appropriate": false, "score": 25, "explanation": "Format issue should be minor not critical"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        finding = {
            "requirement_key": "req_002",
            "requirement_content": "文档格式要求",
            "bid_content": "文档略有格式问题",
            "is_compliant": False,
            "severity": "critical",
            "explanation": "格式不规范",
        }

        result = await evaluator.evaluate_severity_appropriateness(finding)

        assert result.is_appropriate is False
        assert result.score < 70


class TestCompleteness:
    """Tests for completeness evaluation."""

    @pytest.fixture
    def evaluator(self):
        """Create evaluator with mocked LLM."""
        with patch("backend.agent.quality_evaluation.LLMClient") as mock_llm:
            mock_instance = MagicMock()
            mock_llm.return_value = mock_instance
            eval = QualityEvaluator()
            eval._llm_client = mock_instance
            return eval

    @pytest.mark.asyncio
    async def test_all_fields_populated(self, evaluator):
        """EVAL-006: Complete finding with all fields scores high."""
        mock_response = MagicMock()
        mock_response.content = '{"is_complete": true, "score": 95, "missing_fields": [], "explanation": "All required fields present"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        finding = {
            "requirement_key": "req_001",
            "requirement_content": "投标人须具备ISO9001认证",
            "bid_content": "已取得ISO9001认证",
            "is_compliant": True,
            "severity": None,
            "location_page": 5,
            "location_line": 23,
            "suggestion": None,
            "explanation": "认证信息完整",
        }

        result = await evaluator.evaluate_completeness(finding)

        assert result.is_complete is True
        assert result.score >= 70
        assert len(result.missing_fields) == 0

    @pytest.mark.asyncio
    async def test_missing_optional_fields(self, evaluator):
        """EVAL-007: Finding with missing optional fields scores lower."""
        mock_response = MagicMock()
        mock_response.content = '{"is_complete": true, "missing_fields": ["location_page", "suggestion"], "explanation": "Core fields present, optional fields missing"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        finding = {
            "requirement_key": "req_001",
            "requirement_content": "投标人须具备ISO9001认证",
            "bid_content": "已取得ISO9001认证",
            "is_compliant": True,
            "severity": None,
            "location_page": None,
            "location_line": None,
            "suggestion": None,
            "explanation": "认证信息完整",
        }

        result = await evaluator.evaluate_completeness(finding)

        assert result.is_complete is True
        assert result.score >= 50
        assert "location_page" in result.missing_fields or "suggestion" in result.missing_fields


class TestOverallQuality:
    """Tests for overall quality score calculation."""

    @pytest.fixture
    def evaluator(self):
        """Create evaluator with mocked LLM."""
        with patch("backend.agent.quality_evaluation.LLMClient") as mock_llm:
            mock_instance = MagicMock()
            mock_llm.return_value = mock_instance
            eval = QualityEvaluator()
            eval._llm_client = mock_instance
            return eval

    @pytest.mark.asyncio
    async def test_overall_quality_score(self, evaluator):
        """EVAL-008: Overall score is weighted average of criteria."""
        finding = {
            "requirement_key": "req_001",
            "requirement_content": "投标人须具备ISO9001认证",
            "bid_content": "已取得ISO9001认证",
            "is_compliant": True,
            "severity": None,
            "explanation": "认证信息完整",
        }

        # Mock all LLM responses
        mock_response = MagicMock()
        mock_response.content = '{"is_accurate": true, "score": 95, "explanation": "Correct determination"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        overall = await evaluator.evaluate_overall(finding)

        assert overall.overall_score >= 0
        assert overall.overall_score <= 100
        assert overall.compliance_accuracy.score == 95

    @pytest.mark.asyncio
    async def test_empty_findings_list(self, evaluator):
        """EVAL-009: Empty findings list should be handled gracefully."""
        result = await evaluator.evaluate_findings_batch([])

        assert result.total_findings == 0
        assert result.overall_quality_score == 0
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_multiple_findings_quality(self, evaluator):
        """EVAL-010: Multiple findings evaluated correctly."""
        findings = [
            {
                "requirement_key": "req_001",
                "requirement_content": "需要ISO9001认证",
                "bid_content": "已取得ISO9001认证",
                "is_compliant": True,
                "severity": None,
                "explanation": "认证信息完整",
            },
            {
                "requirement_key": "req_002",
                "requirement_content": "交货期30天",
                "bid_content": "交货期45天",
                "is_compliant": False,
                "severity": "major",
                "explanation": "交货期延迟",
            },
        ]

        mock_response = MagicMock()
        mock_response.content = '{"is_accurate": true, "score": 85, "explanation": "Good evaluation"}'
        evaluator._llm_client.generate = AsyncMock(return_value=mock_response)

        result = await evaluator.evaluate_findings_batch(findings)

        assert result.total_findings == 2
        assert len(result.individual_evaluations) == 2
        assert result.overall_quality_score >= 0


class TestEvaluationResult:
    """Tests for evaluation result data classes."""

    def test_finding_evaluation_creation(self):
        """Test FindingEvaluation can be created."""
        eval_result = FindingEvaluation(
            requirement_key="req_001",
            compliance_accuracy=ComplianceAccuracyResult(is_accurate=True, score=90, explanation="Good"),
            severity_appropriateness=SeverityAppropriatenessResult(is_appropriate=True, score=85, explanation="Good"),
            completeness=CompletenessResult(is_complete=True, score=95, missing_fields=[], explanation="Good"),
        )

        assert eval_result.requirement_key == "req_001"
        assert eval_result.compliance_accuracy.score == 90

    def test_evaluation_result_pass_threshold(self):
        """Test EvaluationResult pass/fail threshold."""
        result = EvaluationResult(
            total_findings=1,
            overall_quality_score=70,
            passed=True,
            individual_evaluations=[],
            scores={},
        )

        assert result.passed is True

        result2 = EvaluationResult(
            total_findings=1,
            overall_quality_score=69,
            passed=False,
            individual_evaluations=[],
            scores={},
        )

        assert result2.passed is False
