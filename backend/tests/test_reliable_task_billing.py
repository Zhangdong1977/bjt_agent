"""Regression tests for reliable billable-task lifecycle controls."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.main import app, cleanup_tasks_on_restart
from backend.models import AiUsageRecord, ConsumptionRecord, ReviewTask
from backend.services import billing as billing_service
from backend.services.cost_calculator import estimate_cost
from backend.services import task_lifecycle, usage_recorder


def _scalar(value):
    result = MagicMock()
    result.scalar_one.return_value = value
    result.scalar_one_or_none.return_value = value
    return result


def _usage(task_id: str) -> AiUsageRecord:
    return AiUsageRecord(
        usage_type="llm",
        provider="deepseek",
        status="success",
        user_name="tester",
        task_id=task_id,
        usage_date=__import__("datetime").date.today(),
    )


def test_legacy_review_sessions_route_is_not_exposed():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert not any(path.startswith("/api/review/sessions") for path in paths)


@pytest.mark.asyncio
async def test_global_cleanup_endpoint_fails_closed_without_internal_key():
    with pytest.raises(HTTPException) as exc:
        await cleanup_tasks_on_restart(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_usage_flush_waits_for_scheduled_write():
    task_id = "usage-flush-success"
    completed = []

    async def fake_write(record):
        await asyncio.sleep(0)
        completed.append(record.task_id)

    usage_recorder._pending_writes.pop(task_id, None)
    usage_recorder._write_failures.pop(task_id, None)
    with patch.object(usage_recorder, "_write_one", side_effect=fake_write):
        usage_recorder._spawn(_usage(task_id))
        await usage_recorder.flush_task_usage(task_id)
    assert completed == [task_id]


@pytest.mark.asyncio
async def test_usage_flush_surfaces_write_failure_to_billing_gate():
    task_id = "usage-flush-failure"

    async def fake_write(_record):
        raise RuntimeError("database unavailable")

    usage_recorder._pending_writes.pop(task_id, None)
    usage_recorder._write_failures.pop(task_id, None)
    with patch.object(usage_recorder, "_write_one", side_effect=fake_write):
        usage_recorder._spawn(_usage(task_id))
        with pytest.raises(RuntimeError, match="usage write"):
            await usage_recorder.flush_task_usage(task_id)


def test_vision_usage_cost_uses_provider_token_rate():
    assert estimate_cost(
        provider="volcengine_vision",
        model="doubao-seed-2-0-pro-260215",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        status="success",
    ) == 4.0


def test_task_and_dispatch_outbox_are_prepared_for_one_transaction():
    db = MagicMock()
    row = task_lifecycle.add_task_dispatch(
        db, task_kind="review", task_id="task-1"
    )
    assert row.task_id == "task-1"
    assert row.status == "pending"
    assert row.celery_task_id
    db.add.assert_called_once_with(row)


@pytest.mark.asyncio
async def test_account_level_active_task_limit_blocks_parallel_paid_work():
    wallet = SimpleNamespace(
        recharge_balance_points=Decimal("100"),
        gift_balance_points=Decimal("0"),
        balance_wen=100,
    )
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_scalar(1), _scalar(0)])
    settings = SimpleNamespace(billing_max_active_tasks_per_user=1)
    with patch.object(task_lifecycle, "ensure_wallet", new=AsyncMock(return_value=wallet)), patch.object(
        task_lifecycle, "expire_user_lots", new=AsyncMock(return_value=0)
    ), patch.object(task_lifecycle, "get_settings", return_value=settings):
        with pytest.raises(HTTPException) as exc:
            await task_lifecycle.authorize_billable_task_start(
                db, user_id="user-1", operation_name="AI 检查"
            )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "ACTIVE_BILLING_TASK_EXISTS"
    review_query = str(db.execute.await_args_list[0].args[0])
    assert "review_tasks.billing_status IN" in review_query
    assert "review_tasks.status IN" not in review_query


@pytest.mark.asyncio
async def test_worker_claim_changes_pending_task_to_running_once():
    task = ReviewTask(project_id="project-1", task_type="review", status="pending")
    db = MagicMock()
    db.execute = AsyncMock(return_value=_scalar(task))
    db.commit = AsyncMock()
    claimed = await task_lifecycle.claim_task_for_execution(
        db, task_kind="review", task_id="task-1"
    )
    assert claimed is task
    assert task.status == "running"
    assert task.started_at is not None
    db.commit.assert_awaited_once()


def test_celery_has_outbox_and_reconciliation_schedules():
    schedule = app.state  # import app first to exercise the complete module graph
    assert schedule is not None
    from backend.celery_app import celery_app

    beat = celery_app.conf.beat_schedule
    assert "dispatch-pending-task-outbox" in beat
    assert "reconcile-task-billing" in beat
    assert "expire-credit-lots" in beat


@pytest.mark.asyncio
async def test_failed_task_is_settled_from_actual_usage_cost():
    task = ReviewTask(
        id="task-failed",
        project_id="project-1",
        task_type="review",
        status="failed",
        billing_multiplier=Decimal("4"),
        billing_status="pending",
        billing_attempts=0,
        usage_finalized_at=datetime.now(timezone.utc),
    )
    project = SimpleNamespace(id="project-1", user_id="user-1", name="失败任务项目")
    user = SimpleNamespace(id="user-1", username="tester")
    wallet = SimpleNamespace(
        user_id="user-1",
        balance_wen=100,
        points=0,
        recharge_balance_points=Decimal("100"),
        gift_balance_points=Decimal("0"),
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar(task),
            _scalar(None),
            _scalar(project),
            _scalar(user),
            _scalar(Decimal("0.5")),
            _scalar(wallet),
        ]
    )
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield db

    allocation = {
        "gift_used": Decimal("0"),
        "recharge_used": Decimal("20"),
        "earned_loyalty": 20,
        "before_recharge": Decimal("100"),
        "before_gift": Decimal("0"),
        "after_recharge": Decimal("80"),
        "after_gift": Decimal("0"),
        "folded_income": Decimal("2"),
        "weighted_unit_value": Decimal("0.1"),
    }
    config = SimpleNamespace(sales_multiplier=Decimal("4"))
    with patch.object(billing_service, "async_session_factory", factory), patch.object(
        billing_service, "get_sales_config", new=AsyncMock(return_value=config)
    ), patch.object(
        billing_service, "allocate_consumption", new=AsyncMock(return_value=allocation)
    ):
        record = await billing_service.settle_task_consumption("review", task.id)

    assert record is not None
    assert record.task_status == "failed"
    assert record.cost_cny == Decimal("0.5")
    assert record.cost_points == Decimal("5.000000")
    assert record.sales_points == Decimal("20.00")
    assert task.billing_status == "settled"
    assert any(isinstance(call.args[0], ConsumptionRecord) for call in db.add.call_args_list)
