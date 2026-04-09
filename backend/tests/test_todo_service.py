"""TodoService tests.

Test cases for TodoService CRUD operations.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.services.todo_service import TodoService
from backend.models.todo_item import TodoItem
from backend.models.review_session import ReviewSession


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock()


class TestCreateSession:
    """Tests for create_session method."""

    @pytest.mark.asyncio
    async def test_create_session(self, mock_db):
        """Test creating a review session."""
        service = TodoService(mock_db)
        session = await service.create_session(
            project_id="p1",
            rule_library_path="/rules/",
            tender_doc_path="/docs/tender.md",
            bid_doc_path="/docs/bid.md",
        )
        assert session.project_id == "p1"
        assert session.rule_library_path == "/rules/"
        assert session.tender_doc_path == "/docs/tender.md"
        assert session.bid_doc_path == "/docs/bid.md"
        assert session.status == "pending"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestGetSession:
    """Tests for get_session method."""

    @pytest.mark.asyncio
    async def test_get_session_found(self, mock_db):
        """Test getting an existing session."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ReviewSession(
            project_id="p1",
            rule_library_path="/rules/",
            tender_doc_path="/docs/tender.md",
            bid_doc_path="/docs/bid.md",
            status="pending",
        )
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = TodoService(mock_db)
        session = await service.get_session("s1")

        assert session is not None
        assert session.project_id == "p1"

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, mock_db):
        """Test getting a non-existent session."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = TodoService(mock_db)
        session = await service.get_session("nonexistent")

        assert session is None


class TestUpdateSessionStatus:
    """Tests for update_session_status method."""

    @pytest.mark.asyncio
    async def test_update_session_status(self, mock_db):
        """Test updating session status."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = TodoService(mock_db)
        await service.update_session_status("s1", "completed")

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_session_status_with_result(self, mock_db):
        """Test updating session status with merged result."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = TodoService(mock_db)
        merged_result = {"total": 10, "passed": 8}
        await service.update_session_status("s1", "completed", merged_result=merged_result)

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()


class TestIncrementCompletedTodos:
    """Tests for increment_completed_todos method."""

    @pytest.mark.asyncio
    async def test_increment_completed_todos(self, mock_db):
        """Test incrementing completed todos count."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = TodoService(mock_db)
        await service.increment_completed_todos("s1")

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()


class TestCreateTodo:
    """Tests for create_todo method."""

    @pytest.mark.asyncio
    async def test_create_todo(self, mock_db):
        """Test creating a todo item."""
        service = TodoService(mock_db)
        todo = await service.create_todo(
            project_id="p1",
            session_id="s1",
            rule_doc_path="/rules/rule_001.md",
            rule_doc_name="rule_001.md",
            check_items=[{"id": "1", "title": "检查项"}],
        )
        assert todo.project_id == "p1"
        assert todo.session_id == "s1"
        assert todo.rule_doc_path == "/rules/rule_001.md"
        assert todo.rule_doc_name == "rule_001.md"
        assert todo.status == "pending"
        assert len(todo.check_items) == 1
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestGetTodo:
    """Tests for get_todo method."""

    @pytest.mark.asyncio
    async def test_get_todo_found(self, mock_db):
        """Test getting an existing todo."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = TodoItem(
            project_id="p1",
            session_id="s1",
            rule_doc_path="/rules/rule_001.md",
            rule_doc_name="rule_001.md",
            check_items=[],
            status="pending",
        )
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = TodoService(mock_db)
        todo = await service.get_todo("t1")

        assert todo is not None
        assert todo.project_id == "p1"

    @pytest.mark.asyncio
    async def test_get_todo_not_found(self, mock_db):
        """Test getting a non-existent todo."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = TodoService(mock_db)
        todo = await service.get_todo("nonexistent")

        assert todo is None


class TestGetSessionTodos:
    """Tests for get_session_todos method."""

    @pytest.mark.asyncio
    async def test_get_session_todos(self, mock_db):
        """Test getting all todos for a session."""
        mock_todos = [
            TodoItem(
                project_id="p1",
                session_id="s1",
                rule_doc_path="/rules/rule_001.md",
                rule_doc_name="rule_001.md",
                check_items=[],
                status="pending",
            ),
            TodoItem(
                project_id="p1",
                session_id="s1",
                rule_doc_path="/rules/rule_002.md",
                rule_doc_name="rule_002.md",
                check_items=[],
                status="completed",
            ),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_todos
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = TodoService(mock_db)
        todos = await service.get_session_todos("s1")

        assert len(todos) == 2


class TestUpdateTodoStatus:
    """Tests for update_todo_status method."""

    @pytest.mark.asyncio
    async def test_update_todo_status(self, mock_db):
        """Test updating todo status."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = TodoService(mock_db)
        await service.update_todo_status("t1", "completed")

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_todo_status_with_result(self, mock_db):
        """Test updating todo status with result."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = TodoService(mock_db)
        result = {"findings": ["发现1", "发现2"]}
        await service.update_todo_status("t1", "completed", result=result)

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_todo_status_with_error(self, mock_db):
        """Test updating todo status with error message."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = TodoService(mock_db)
        await service.update_todo_status("t1", "failed", error_message="Something went wrong")

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()


class TestResetTodoForRetry:
    """Tests for reset_todo_for_retry method."""

    @pytest.mark.asyncio
    async def test_reset_todo_for_retry(self, mock_db):
        """Test resetting todo for retry."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = TodoService(mock_db)
        await service.reset_todo_for_retry("t1", retry_count=1)

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()