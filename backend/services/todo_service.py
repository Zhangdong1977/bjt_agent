"""TodoService for TodoItem and ReviewSession CRUD operations."""

from typing import Optional
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.todo_item import TodoItem
from backend.models.review_session import ReviewSession


class TodoService:
    """Todo related CRUD operations service."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # === ReviewSession operations ===

    async def create_session(
        self,
        project_id: str,
        rule_library_path: str,
        tender_doc_path: str,
        bid_doc_path: str,
    ) -> ReviewSession:
        """Create a review session."""
        session = ReviewSession(
            project_id=project_id,
            rule_library_path=rule_library_path,
            tender_doc_path=tender_doc_path,
            bid_doc_path=bid_doc_path,
            status="pending",
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def get_session(self, session_id: str) -> Optional[ReviewSession]:
        """Get a review session by ID."""
        result = await self.db.execute(
            select(ReviewSession).where(ReviewSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def update_session_status(
        self,
        session_id: str,
        status: str,
        merged_result: Optional[dict] = None,
    ) -> None:
        """Update session status."""
        update_data = {"status": status, "updated_at": datetime.now()}
        if merged_result is not None:
            update_data["merged_result"] = merged_result
        if status == "completed":
            update_data["completed_at"] = datetime.now()
        await self.db.execute(
            update(ReviewSession).where(ReviewSession.id == session_id).values(**update_data)
        )
        await self.db.commit()

    async def increment_completed_todos(self, session_id: str) -> None:
        """Increment the completed todos count."""
        await self.db.execute(
            update(ReviewSession)
            .where(ReviewSession.id == session_id)
            .values(
                completed_todos=ReviewSession.completed_todos + 1,
                updated_at=datetime.now(),
            )
        )
        await self.db.commit()

    # === TodoItem operations ===

    async def create_todo(
        self,
        project_id: str,
        session_id: str,
        rule_doc_path: str,
        rule_doc_name: str,
        check_items: Optional[list] = None,
    ) -> TodoItem:
        """Create a todo item."""
        todo = TodoItem(
            project_id=project_id,
            session_id=session_id,
            rule_doc_path=rule_doc_path,
            rule_doc_name=rule_doc_name,
            check_items=check_items,
            status="pending",
        )
        self.db.add(todo)
        await self.db.commit()
        await self.db.refresh(todo)
        return todo

    async def get_todo(self, todo_id: str) -> Optional[TodoItem]:
        """Get a todo item by ID."""
        result = await self.db.execute(
            select(TodoItem).where(TodoItem.id == todo_id)
        )
        return result.scalar_one_or_none()

    async def get_session_todos(self, session_id: str) -> list[TodoItem]:
        """Get all todos for a session."""
        result = await self.db.execute(
            select(TodoItem)
            .where(TodoItem.session_id == session_id)
            .order_by(TodoItem.created_at)
        )
        return list(result.scalars().all())

    async def update_todo_status(
        self,
        todo_id: str,
        status: str,
        result: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update todo status."""
        update_data = {"status": status, "updated_at": datetime.now()}
        if status == "running":
            update_data["started_at"] = datetime.now()
        if status == "completed":
            update_data["completed_at"] = datetime.now()
        if result is not None:
            update_data["result"] = result
        if error_message is not None:
            update_data["error_message"] = error_message
        await self.db.execute(
            update(TodoItem).where(TodoItem.id == todo_id).values(**update_data)
        )
        await self.db.commit()

    async def reset_todo_for_retry(self, todo_id: str, retry_count: int) -> None:
        """Reset todo for retry."""
        await self.db.execute(
            update(TodoItem)
            .where(TodoItem.id == todo_id)
            .values(
                status="pending",
                result=None,
                error_message=None,
                retry_count=retry_count,
                updated_at=datetime.now(),
            )
        )
        await self.db.commit()