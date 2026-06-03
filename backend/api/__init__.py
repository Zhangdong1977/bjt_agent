# API routes
from .auth import router as auth_router
from .projects import router as projects_router
from .documents import router as documents_router
from .review import router as review_router
from .review_sessions import router as review_sessions_router
from .knowledge import router as knowledge_router
from .feedback import router as feedback_router
from .experience import router as experience_router

__all__ = [
    "auth_router",
    "projects_router",
    "documents_router",
    "review_router",
    "review_sessions_router",
    "knowledge_router",
    "feedback_router",
    "experience_router",
]
