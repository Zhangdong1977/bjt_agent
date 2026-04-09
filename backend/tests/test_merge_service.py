# backend/tests/test_merge_service.py
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Mock the models module before importing merge_service
mock_models = MagicMock()
sys.modules['backend.models'] = mock_models

# Also need to mock sqlalchemy delete and insert functions
mock_delete = MagicMock(return_value=MagicMock(where=MagicMock(return_value=MagicMock())))
mock_insert = MagicMock(return_value=MagicMock())

from backend.services.merge_service import MergeService


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.decide_merge = AsyncMock()
    return agent


@pytest.fixture
def mock_db():
    return MagicMock()


class TestMergeServiceLLMDecision:
    """Test MergeService with LLM-based merge decisions."""

    @pytest.mark.asyncio
    async def test_llm_decision_keep(self, mock_agent, mock_db):
        """When LLM says keep, new finding should be added."""
        mock_agent.decide_merge.return_value = "决策：keep\n理由：新发现是全新的\n替换key：无"

        service = MergeService(mock_db, mock_agent)

        new_finding = {
            "requirement_key": "req_001",
            "requirement_content": "新要求内容",
        }

        decision = await service._get_llm_merge_decision(new_finding, [])

        assert decision["action"] == "keep"
        assert decision["parse_failed"] is False

    @pytest.mark.asyncio
    async def test_llm_decision_replace(self, mock_agent, mock_db):
        """When LLM says replace, should identify the key to replace."""
        mock_agent.decide_merge.return_value = "决策：replace\n理由：新发现更完整\n替换key：req_001"

        service = MergeService(mock_db, mock_agent)

        decision = await service._get_llm_merge_decision({}, [])

        assert decision["action"] == "replace"
        assert decision["replace_key"] == "req_001"

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self, mock_agent, mock_db):
        """When LLM call fails, should use keep_both strategy."""
        mock_agent.decide_merge.side_effect = Exception("LLM API error")

        service = MergeService(mock_db, mock_agent)

        decision = await service._get_llm_merge_decision({}, [])

        assert decision["action"] == "keep_both"
        assert decision["parse_failed"] is True

    @pytest.mark.asyncio
    async def test_keep_both_on_parse(self, mock_agent, mock_db):
        """When LLM says keep_both, both records should be kept."""
        # This simulates when parser returns keep_both (parse_failed case handled separately)
        mock_agent.decide_merge.return_value = "some unparseable text"

        service = MergeService(mock_db, mock_agent)

        # The fallback (keep_both) happens on parse failure
        decision = await service._get_llm_merge_decision({}, [{"key": "val"}])

        assert decision["action"] == "keep_both"

    @pytest.mark.asyncio
    async def test_keep_action_logic(self, mock_agent, mock_db):
        """When keep action is returned, should only add new record with generated key."""
        mock_agent.decide_merge.return_value = "决策：keep\n理由：新发现是全新的\n替换key：无"

        service = MergeService(mock_db, mock_agent)

        new_finding = {
            "requirement_key": "new_001",
            "requirement_content": "新要求内容",
        }
        existing_findings = [
            {"requirement_key": "req_001", "requirement_content": "要求1"},
            {"requirement_key": "req_002", "requirement_content": "要求2"},
            {"requirement_key": "req_003", "requirement_content": "要求3"},
        ]

        decision = await service._get_llm_merge_decision(new_finding, existing_findings)

        assert decision["action"] == "keep"

        # Test _generate_new_requirement_key
        new_key = service._generate_new_requirement_key(existing_findings)
        assert new_key == "req_004"

        # Verify keep does NOT duplicate existing records
        assert len(existing_findings) == 3  # unchanged

    @pytest.mark.asyncio
    async def test_keep_generates_new_key(self, mock_agent, mock_db):
        """When LLM says keep, should generate new key like req_004."""
        mock_agent.decide_merge.return_value = "决策：keep\n理由：新发现是全新的\n替换key：无"

        service = MergeService(mock_db, mock_agent)

        new_finding = {
            "requirement_key": "new_001",
            "requirement_content": "新要求内容",
        }
        existing_findings = [
            {"requirement_key": "req_001", "requirement_content": "要求1"},
            {"requirement_key": "req_002", "requirement_content": "要求2"},
            {"requirement_key": "req_003", "requirement_content": "要求3"},
        ]

        decision = await service._get_llm_merge_decision(new_finding, existing_findings)

        assert decision["action"] == "keep"

        # Test _generate_new_requirement_key
        new_key = service._generate_new_requirement_key(existing_findings)
        assert new_key == "req_004"

    @pytest.mark.asyncio
    async def test_replace_updates_target_record(self, mock_agent, mock_db):
        """When LLM says replace req_002, should update req_002 content."""
        mock_agent.decide_merge.return_value = "决策：replace\n理由：新发现更完整\n替换key：req_002"

        service = MergeService(mock_db, mock_agent)

        new_finding = {
            "requirement_key": "new_001",
            "requirement_content": "新的更完整的内容",
        }

        decision = await service._get_llm_merge_decision(new_finding, [])

        assert decision["action"] == "replace"
        assert decision["replace_key"] == "req_002"

    @pytest.mark.asyncio
    async def test_discard_ignores_new_finding(self, mock_agent, mock_db):
        """When LLM says discard, should not add new record."""
        mock_agent.decide_merge.return_value = "决策：discard\n理由：重复内容\n替换key：无"

        service = MergeService(mock_db, mock_agent)

        decision = await service._get_llm_merge_decision({}, [])

        assert decision["action"] == "discard"

    @pytest.mark.asyncio
    @patch('backend.services.merge_service.delete')
    async def test_full_merge_flow_with_sequential_new_results(self, mock_delete, mock_agent, mock_db):
        """Test: existing [req_001, req_002, req_003], new task [new_001, new_002]

        Flow:
        1. new_001 vs [req_001, req_002, req_003] → keep → adds as req_004
        2. new_002 vs [req_001, req_002, req_003, req_004] → replace req_002
        """
        # 模拟 LLM 决策
        call_log = []
        async def mock_decide(new_finding, existing):
            key = new_finding.get("requirement_key", "")
            existing_keys = [r.get("requirement_key") for r in existing]
            call_log.append({"key": key, "existing_keys": existing_keys})

            if key == "new_001":
                # 验证 new_001 对比时 existing 包含 req_001, req_002, req_003
                assert "req_001" in existing_keys, f"new_001 should see req_001, got {existing_keys}"
                assert "req_002" in existing_keys, f"new_001 should see req_002, got {existing_keys}"
                assert "req_003" in existing_keys, f"new_001 should see req_003, got {existing_keys}"
                return "决策：keep\n理由：新发现全新\n替换key：无"
            elif key == "new_002":
                # 验证 new_002 对比时 existing 包含 req_001, req_002, req_003, req_004
                assert "req_001" in existing_keys, f"new_002 should see req_001, got {existing_keys}"
                assert "req_002" in existing_keys, f"new_002 should see req_002, got {existing_keys}"
                assert "req_003" in existing_keys, f"new_002 should see req_003, got {existing_keys}"
                assert "req_004" in existing_keys, f"new_002 should see newly added req_004, got {existing_keys}"
                return "决策：replace\n理由：更新req_002\n替换key：req_002"
            return "决策：keep\n理由：默认\n替换key：无"

        mock_agent.decide_merge = AsyncMock(side_effect=mock_decide)

        # Mock db.execute to return proper SQLAlchemy result objects
        mock_execute_result = MagicMock()
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_execute_result)
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        service = MergeService(mock_db, mock_agent)

        # Mock existing merged records
        existing_merged = [
            {"requirement_key": "req_001", "requirement_content": "内容1", "task_id": "t1",
             "bid_content": "bid1", "is_compliant": False, "severity": "major",
             "location_page": 1, "location_line": 10, "suggestion": "s1", "explanation": "e1"},
            {"requirement_key": "req_002", "requirement_content": "内容2", "task_id": "t1",
             "bid_content": "bid2", "is_compliant": False, "severity": "minor",
             "location_page": 2, "location_line": 20, "suggestion": "s2", "explanation": "e2"},
            {"requirement_key": "req_003", "requirement_content": "内容3", "task_id": "t1",
             "bid_content": "bid3", "is_compliant": True, "severity": None,
             "location_page": 3, "location_line": 30, "suggestion": None, "explanation": "e3"},
        ]

        # Mock _get_existing_merged
        async def mock_get_existing(project_id):
            return existing_merged
        service._get_existing_merged = mock_get_existing

        # Mock new results
        latest_results = [
            {"requirement_key": "new_001", "requirement_content": "新内容1", "task_id": "t2",
             "bid_content": "new_bid1", "is_compliant": False, "severity": "critical",
             "location_page": 5, "location_line": 50, "suggestion": "ns1", "explanation": "ne1"},
            {"requirement_key": "new_002", "requirement_content": "新内容2-更新", "task_id": "t2",
             "bid_content": "new_bid2", "is_compliant": False, "severity": "major",
             "location_page": 6, "location_line": 60, "suggestion": "ns2", "explanation": "ne2"},
        ]

        # Mock _get_historical_results
        async def mock_get_historical(project_id):
            return existing_merged + latest_results
        service._get_historical_results = mock_get_historical

        # 执行合并
        merge_count, total_count = await service.merge_project_results(
            project_id="p1",
            latest_task_id="t2"
        )

        # 验证
        assert merge_count == 2  # new_001 keep, new_002 replace
        # 最终应该有: req_001, req_002(被更新), req_003, req_004(new_001 keep)
        assert total_count == 4

        # 验证 call_log 确认两个 new 结果都经过了 LLM 对比
        assert len(call_log) == 2, f"Expected 2 LLM calls, got {len(call_log)}"
        assert call_log[0]["key"] == "new_001"
        assert call_log[1]["key"] == "new_002"


class TestIsDuplicateContent:
    """Test _is_duplicate_content method for detecting duplicate findings."""

    @pytest.fixture
    def service(self, mock_db):
        return MergeService(mock_db)

    def test_same_compliant_true_is_duplicate(self, service):
        """Both compliant with same bid_content should be duplicate."""
        new = {
            "is_compliant": True,
            "bid_content": "已取得ISO9001认证",
            "explanation": "证书齐全",
        }
        existing = {
            "is_compliant": True,
            "bid_content": "已取得ISO9001认证",
            "explanation": "证书齐全",
        }
        assert service._is_duplicate_content(new, existing) is True

    def test_different_bid_content_not_duplicate(self, service):
        """Different bid_content should not be duplicate."""
        new = {
            "is_compliant": True,
            "bid_content": "已取得ISO9001认证",
            "explanation": "证书齐全",
        }
        existing = {
            "is_compliant": True,
            "bid_content": "已取得ISO14001认证",
            "explanation": "证书齐全",
        }
        assert service._is_duplicate_content(new, existing) is False

    def test_different_compliance_not_duplicate(self, service):
        """One compliant one not should not be duplicate."""
        new = {
            "is_compliant": False,
            "severity": "major",
            "bid_content": "交货期45天",
            "explanation": "超过要求",
        }
        existing = {
            "is_compliant": True,
            "bid_content": "交货期45天",
            "explanation": "超过要求",
        }
        assert service._is_duplicate_content(new, existing) is False

    def test_different_severity_not_duplicate(self, service):
        """Different severity should not be duplicate."""
        new = {
            "is_compliant": False,
            "severity": "critical",
            "bid_content": "缺少资质证书",
            "explanation": "未提供",
        }
        existing = {
            "is_compliant": False,
            "severity": "major",
            "bid_content": "缺少资质证书",
            "explanation": "未提供",
        }
        assert service._is_duplicate_content(new, existing) is False

    def test_different_explanation_not_duplicate(self, service):
        """Different explanation should not be duplicate."""
        new = {
            "is_compliant": False,
            "severity": "major",
            "bid_content": "交货期45天",
            "explanation": "超过要求30天",
        }
        existing = {
            "is_compliant": False,
            "severity": "major",
            "bid_content": "交货期45天",
            "explanation": "超过要求15天",
        }
        assert service._is_duplicate_content(new, existing) is False

    def test_same_non_compliant_is_duplicate(self, service):
        """Same non-compliant findings should be duplicate."""
        new = {
            "is_compliant": False,
            "severity": "major",
            "bid_content": "交货期45天",
            "explanation": "超过要求30天",
        }
        existing = {
            "is_compliant": False,
            "severity": "major",
            "bid_content": "交货期45天",
            "explanation": "超过要求30天",
        }
        assert service._is_duplicate_content(new, existing) is True
