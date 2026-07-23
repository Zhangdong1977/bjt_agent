# API routes
from .auth import router as auth_router
from .projects import router as projects_router
from .documents import router as documents_router
from .documents import drafts_router as documents_drafts_router
from .review import router as review_router
from .duplicate_check import router as duplicate_check_router
from .review_sessions import router as review_sessions_router
from .share import router as share_router
from .knowledge import router as knowledge_router
from .feedback import router as feedback_router
from .experience import router as experience_router
from .admin import router as admin_router
from .profile import router as profile_router
from .billing import router as billing_router
from .announcements import router as announcements_router
from .system_status import router as system_status_router

__all__ = [
    "auth_router",
    "projects_router",
    "documents_router",
    "documents_drafts_router",
    "review_router",
    "duplicate_check_router",
    "review_sessions_router",
    "share_router",
    "knowledge_router",
    "feedback_router",
    "experience_router",
    "admin_router",
    "profile_router",
    "billing_router",
    "announcements_router",
    "system_status_router",
]
