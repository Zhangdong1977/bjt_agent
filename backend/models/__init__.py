# Database models
from .base import Base, get_db_session, engine, async_session_factory, init_db, close_db
from .user import User
from .project import Project
from .document import Document
from .review_task import ReviewTask
from .review_result import ReviewResult
from .duplicate_result import DuplicateResult
from .duplicate_document_member import DuplicateDocumentMember
from .duplicate_occurrence import DuplicateOccurrence
from .duplicate_pair_summary import DuplicatePairSummary
from .duplicate_evidence_cluster import DuplicateEvidenceCluster
from .project_review_result import ProjectReviewResult
from .agent_step import AgentStep
from .todo_item import TodoItem
from .review_share import ReviewShareToken
from .review_session import ReviewSession
from .ai_usage_record import AiUsageRecord
from .ai_usage_task_summary import AiUsageTaskSummary
from .billing import BillingOrder, ConsumptionRecord, TaskDispatchOutbox, UserWallet, WalletTransaction
from .sales import (
    SalesConfig,
    SalesPackage,
    GrantBatch,
    CreditLot,
    PointLedgerEntry,
    ConsumptionAllocation,
)
from .announcement import SystemAnnouncement, SystemAnnouncementRead
from .system_maintenance import SystemMaintenance, MAINTENANCE_ROW_ID
from .api_key import ApiKey
from .blind_check_task import BlindCheckTask
from .vsto_tool_session import VstoToolSession
from .vsto_tool_call import VstoToolCall
from .blind_check_finding import BlindCheckFinding
from .bid_draft_task import BidDraftTask
from .bid_draft_section import BidDraftSection
from .polish_task import PolishTask
from backend.experience.models import ExperienceFeedback, ExperienceCase, ExperienceSkill, ExperienceClusterMembership

# Billable top-level task registry: task kind -> ORM model. Shared by
# task_lifecycle / billing / billing_tasks so new kinds register in one place.
TASK_MODEL_BY_KIND = {
    "review": ReviewTask,
    "duplicate": ReviewTask,
    "blind_check": BlindCheckTask,
    "bid_draft": BidDraftTask,
    "polish": PolishTask,
}

__all__ = [
    "Base",
    "get_db_session",
    "engine",
    "async_session_factory",
    "init_db",
    "close_db",
    "User",
    "Project",
    "Document",
    "ReviewTask",
    "ReviewResult",
    "DuplicateResult",
    "DuplicateDocumentMember",
    "DuplicateOccurrence",
    "DuplicatePairSummary",
    "DuplicateEvidenceCluster",
    "ProjectReviewResult",
    "AgentStep",
    "TodoItem",
    "ReviewShareToken",
    "ReviewSession",
    "AiUsageRecord",
    "AiUsageTaskSummary",
    "BillingOrder",
    "ConsumptionRecord",
    "TaskDispatchOutbox",
    "UserWallet",
    "WalletTransaction",
    "SalesConfig",
    "SalesPackage",
    "GrantBatch",
    "CreditLot",
    "PointLedgerEntry",
    "ConsumptionAllocation",
    "SystemAnnouncement",
    "SystemAnnouncementRead",
    "SystemMaintenance",
    "MAINTENANCE_ROW_ID",
    "ApiKey",
    "BlindCheckTask",
    "VstoToolSession",
    "VstoToolCall",
    "BlindCheckFinding",
    "BidDraftTask",
    "BidDraftSection",
    "PolishTask",
    "TASK_MODEL_BY_KIND",
    "ExperienceFeedback",
    "ExperienceCase",
    "ExperienceSkill",
    "ExperienceClusterMembership",
]
