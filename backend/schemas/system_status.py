"""Pydantic schemas for the system-status feature."""

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


class MaintenancePublicResponse(BaseModel):
    """公开（未登录可读）维护态——登录页横幅用，仅暴露必要字段。"""

    is_enabled: bool
    reason: str = ""
    started_at: datetime | None = None


class MaintenanceStateResponse(BaseModel):
    """维护态完整视图（内部用户管理端）。"""

    is_enabled: bool
    reason: str = ""
    started_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


class MaintenanceUpdateRequest(BaseModel):
    """切换维护模式（内部用户）。"""

    enabled: bool
    reason: str = Field(default="", max_length=500)


# ---------------------------------------------------------------------------
# Cluster / workers
# ---------------------------------------------------------------------------


class WorkerInfo(BaseModel):
    """单个 celery worker 的实时状态。"""

    name: str
    node: str
    role: str  # review | parser | standalone
    index: int | None = None
    alive: bool
    active_review_tasks: int
    active_parser_tasks: int
    processed: int
    uptime: float | None = None


class NodeInfo(BaseModel):
    """一个集群节点的聚合状态。"""

    name: str
    label: str
    roles: list[str] = []
    alive_workers: int
    total_workers: int
    active_review_tasks: int
    active_parser_tasks: int
    processed: int
    is_online: bool


class QueueDepths(BaseModel):
    """celery 队列积压（broker 不可达时为 None）。"""

    review: int | None = None
    parser: int | None = None


class SystemOverview(BaseModel):
    """全局在途任务 / 队列 / worker 概览。"""

    running_reviews: int
    parsing_documents: int
    review_queue: int | None = None
    parser_queue: int | None = None
    alive_workers: int
    total_workers: int
    degraded: bool = False  # celery/broker 探测全空时为 True


class SystemStatusResponse(BaseModel):
    """系统状态页一次拿全：维护态 + 概览 + 节点明细。"""

    maintenance: MaintenanceStateResponse
    overview: SystemOverview
    nodes: list[NodeInfo]
    workers: list[WorkerInfo]
    queue_depths: QueueDepths
