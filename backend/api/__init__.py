# API routes
from .auth import router as auth_router
from .projects import router as projects_router
from .documents import router as documents_router
from .review import router as review_router

__all__ = [
    "auth_router",
    "projects_router",
    "documents_router",
    "review_router",
]
