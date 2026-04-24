"""Tests for SubAgentExecutor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agent.master.sub_agent_executor import SubAgentExecutor, detect_anomaly


def test_detect_anomaly_empty_result():
    """Test anomaly detection with empty result."""
    assert detect_anomaly({}, None) is True
    assert detect_anomaly({"success": True, "findings": []}, None) is True


def test_detect_anomaly_failed_result():
    """Test anomaly detection with failed result."""
    result = {"success": False, "error": "Some error occurred"}
    assert detect_anomaly(result, None) is True


def test_detect_anomaly_empty_findings():
    """Test anomaly detection when findings list is empty."""
    result = {
        "success": True,
        "findings": [],
    }
    assert detect_anomaly(result, None) is True


def test_detect_anomaly_all_compliant_complete():
    """Test no anomaly when all findings are compliant and complete."""
    todo = type("TodoItem", (), {"check_items": [{"id": 1}, {"id": 2}, {"id": 3}]})()
    result = {
        "success": True,
        "findings": [
            {"is_compliant": True, "requirement_key": "1"},
            {"is_compliant": True, "requirement_key": "2"},
            {"is_compliant": True, "requirement_key": "3"},
        ],
    }
    assert detect_anomaly(result, todo) is False


def test_detect_anomaly_has_non_compliant():
    """Test no anomaly when there are non-compliant findings."""
    todo = type("TodoItem", (), {"check_items": [{"id": 1}, {"id": 2}]})()
    result = {
        "success": True,
        "findings": [
            {"is_compliant": True, "requirement_key": "1"},
            {"is_compliant": False, "requirement_key": "2"},
        ],
    }
    assert detect_anomaly(result, todo) is False


class TestSubAgentExecutor:
    """Tests for SubAgentExecutor class."""

    @pytest.fixture
    def mock_todo_item(self):
        """Create a mock TodoItem."""
        todo = MagicMock()
        todo.id = "todo_123"
        todo.project_id = "project_456"
        todo.rule_doc_name = "test_rules.md"
        todo.rule_doc_path = "/path/to/rules.md"
        todo.check_items = [
            {"title": "Check 1", "description": "First check", "rule_content": "Rule 1 content"},
            {"title": "Check 2", "description": "Second check", "rule_content": "Rule 2 content"},
        ]
        return todo

    @pytest.fixture
    def executor(self, mock_todo_item):
        """Create a SubAgentExecutor instance."""
        return SubAgentExecutor(
            todo_item=mock_todo_item,
            tender_doc_path="/path/to/tender.md",
            bid_doc_path="/path/to/bid.md",
            user_id="user_789",
            session_factory=None,
            event_callback=MagicMock(),
        )

    def test_send_event_calls_callback(self, executor):
        """Test _send_event calls the callback when provided."""
        callback = MagicMock()
        executor.event_callback = callback

        executor._send_event("test_event", {"key": "value"})

        callback.assert_called_once_with("test_event", {"key": "value"})

    def test_send_event_no_callback(self, executor):
        """Test _send_event does not raise when no callback provided."""
        executor.event_callback = None

        # Should not raise
        executor._send_event("test_event", {"key": "value"})


    @pytest.mark.asyncio
    async def test_create_agent(self, executor):
        """Test create_agent creates BidReviewAgent with correct params."""
        with patch("backend.agent.master.sub_agent_executor.BidReviewAgent") as MockAgent:
            mock_agent = AsyncMock()
            mock_agent.initialize = AsyncMock()
            MockAgent.return_value = mock_agent

            agent = await executor.create_agent(max_steps=75)

            MockAgent.assert_called_once_with(
                project_id="project_456",
                tender_doc_path="/path/to/tender.md",
                bid_doc_path="/path/to/bid.md",
                user_id="user_789",
                rule_doc_path="/path/to/rules.md",
                event_callback=executor.event_callback,
                logger=None,
                max_steps=75,
            )
            mock_agent.initialize.assert_called_once()
            assert executor._agent == mock_agent
            assert agent == mock_agent

    @pytest.mark.asyncio
    async def test_create_agent_default_max_steps(self, executor):
        """Test create_agent uses default max_steps of 100."""
        with patch("backend.agent.master.sub_agent_executor.BidReviewAgent") as MockAgent:
            mock_agent = AsyncMock()
            mock_agent.initialize = AsyncMock()
            MockAgent.return_value = mock_agent

            await executor.create_agent()

            MockAgent.assert_called_once()
            call_kwargs = MockAgent.call_args.kwargs
            assert call_kwargs["max_steps"] == 100

    @pytest.mark.asyncio
    async def test_execute_returns_correct_structure(self, executor):
        """Test execute returns correct dict structure on success."""
        mock_findings = [
            {"requirement_key": "req_001", "is_compliant": False, "requirement_content": "Test requirement"}
        ]

        with patch.object(executor, "create_agent", new_callable=AsyncMock) as mock_create:
            mock_agent = AsyncMock()
            mock_agent.run_review = AsyncMock(return_value=mock_findings)
            mock_agent.close = AsyncMock()
            mock_agent.add_user_message = MagicMock()
            mock_create.return_value = mock_agent

            result = await executor.execute(max_steps=30)

            assert result["success"] is True
            assert result["findings"] == mock_findings
            assert result["todo_id"] == "todo_123"

    @pytest.mark.asyncio
    async def test_execute_handles_exception(self, executor):
        """Test execute returns error dict on exception."""
        with patch.object(executor, "create_agent", new_callable=AsyncMock) as mock_create:
            mock_agent = AsyncMock()
            mock_agent.run_review = AsyncMock(side_effect=RuntimeError("Test error"))
            mock_agent.close = AsyncMock()
            mock_create.return_value = mock_agent

            result = await executor.execute()

            assert result["success"] is False
            assert "Test error" in result["error"]
            assert result["todo_id"] == "todo_123"

    @pytest.mark.asyncio
    async def test_close(self, executor):
        """Test close calls agent.close()."""
        mock_agent = AsyncMock()
        mock_agent.close = AsyncMock()
        executor._agent = mock_agent

        await executor.close()

        mock_agent.close.assert_called_once()
        assert executor._agent is None

    @pytest.mark.asyncio
    async def test_close_no_agent(self, executor):
        """Test close does nothing when no agent exists."""
        executor._agent = None

        # Should not raise
        await executor.close()