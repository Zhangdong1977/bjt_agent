"""Review sessions API routes."""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.api.deps import DBSession, CurrentUser
from backend.services.todo_service import TodoService
from backend.agent.master.master_agent import MasterAgent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/review/sessions", tags=["review"])


class CreateSessionRequest(BaseModel):
    project_id: str
    rule_library_path: str
    tender_doc_path: str
    bid_doc_path: str
    max_parallel: int = 5
    max_retries: int = 3


class SessionResponse(BaseModel):
    session_id: str
    status: str
    total_todos: int


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    req: CreateSessionRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> SessionResponse:
    """创建审查会话"""
    service = TodoService(db)
    session = await service.create_session(
        project_id=req.project_id,
        rule_library_path=req.rule_library_path,
        tender_doc_path=req.tender_doc_path,
        bid_doc_path=req.bid_doc_path,
    )
    return SessionResponse(
        session_id=session.id,
        status=session.status,
        total_todos=session.total_todos,
    )


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """获取会话详情"""
    service = TodoService(db)
    session = await service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


@router.get("/{session_id}/progress")
async def get_progress(
    session_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """获取审查进度"""
    service = TodoService(db)
    session = await service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    todos = await service.get_session_todos(session_id)
    return {
        "session_id": session_id,
        "status": session.status,
        "total_todos": len(todos),
        "completed_todos": session.completed_todos,
        "todos": [t.to_dict() for t in todos],
    }


@router.get("/{session_id}/result")
async def get_result(
    session_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """获取审查结果"""
    service = TodoService(db)
    session = await service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session_id,
        "status": session.status,
        "merged_result": session.merged_result,
    }


@router.post("/{session_id}/start")
async def start_review(
    session_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """启动审查（后台执行）"""
    from backend.main import app

    service = TodoService(db)
    session = await service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 获取 SSE event_callback（从 app state 获取）
    event_callback = getattr(app.state, "sse_callbacks", {}).get(session_id)

    async def run_master() -> None:
        try:
            master = MasterAgent(
                project_id=session.project_id,
                rule_library_path=session.rule_library_path,
                tender_doc_path=session.tender_doc_path,
                bid_doc_path=session.bid_doc_path,
                user_id="system",
                event_callback=event_callback,
            )
            result = await master.run(service, session_id)
            await service.update_session_status(
                session_id,
                "completed" if result["success"] else "failed",
                result.get("merged_result"),
            )
        except Exception as e:
            logger.exception(f"Review failed for session {session_id}: {e}")
            await service.update_session_status(session_id, "failed", None)

    # 后台执行
    try:
        asyncio.create_task(run_master())
    except Exception as e:
        logger.exception(f"Failed to start review task for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to start review")

    return {"message": "Review started", "session_id": session_id}