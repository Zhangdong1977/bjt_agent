"""TodoItem model tests.

Test cases:
- TODO-001: TodoItem creation with required fields
- TODO-002: TodoItem to_dict method
- TODO-003: TodoItem with explicit status values
"""

import pytest
from backend.models.todo_item import TodoItem


class TestTodoItemCreation:
    """Tests for TodoItem model creation."""

    def test_todo_item_creation(self):
        """TODO-001: TodoItem creation with required fields."""
        todo = TodoItem(
            project_id="test-project",
            session_id="test-session",
            rule_doc_path="/rules/test.md",
            rule_doc_name="test.md",
            check_items=[{"id": "1", "title": "检查项1"}],
        )
        assert todo.project_id == "test-project"
        assert todo.session_id == "test-session"
        assert todo.rule_doc_path == "/rules/test.md"
        assert todo.rule_doc_name == "test.md"
        assert len(todo.check_items) == 1
        assert todo.check_items[0]["title"] == "检查项1"

    def test_todo_item_with_explicit_status(self):
        """TODO-003: TodoItem with explicit status values."""
        todo = TodoItem(
            project_id="test-project",
            session_id="test-session",
            rule_doc_path="/rules/test.md",
            rule_doc_name="test.md",
            status="running",
            retry_count=1,
            max_retries=5,
        )
        assert todo.status == "running"
        assert todo.retry_count == 1
        assert todo.max_retries == 5


class TestTodoItemToDict:
    """Tests for TodoItem to_dict method."""

    def test_todo_item_to_dict(self):
        """TODO-002: TodoItem to_dict method."""
        todo = TodoItem(
            project_id="test-project",
            session_id="test-session",
            rule_doc_path="/rules/test.md",
            rule_doc_name="test.md",
            check_items=[{"id": "1", "title": "检查项1"}],
            status="pending",
        )
        d = todo.to_dict()
        assert d["project_id"] == "test-project"
        assert d["session_id"] == "test-session"
        assert d["rule_doc_path"] == "/rules/test.md"
        assert d["rule_doc_name"] == "test.md"
        assert d["status"] == "pending"
        assert "id" in d
        assert "created_at" in d
        assert "updated_at" in d

    def test_todo_item_to_dict_with_optional_fields(self):
        """TODO-002: TodoItem to_dict with optional fields."""
        todo = TodoItem(
            project_id="test-project",
            session_id="test-session",
            rule_doc_path="/rules/test.md",
            rule_doc_name="test.md",
            status="completed",
            result={"findings": ["发现1", "发现2"]},
            retry_count=2,
        )
        d = todo.to_dict()
        assert d["status"] == "completed"
        assert d["result"] == {"findings": ["发现1", "发现2"]}
        assert d["retry_count"] == 2
        assert d["error_message"] is None