"""expire_pending_recharge_orders 兜底清理任务的单元测试。

覆盖两类 poll 任务扫不到的过期 pending 单：
- 未取交行码（external_order_no NULL）→ 不可能已付款，直接取消 + 释放优惠券；
- 已取码 → 先查交行：success 补单（allow_expired_if_paid），否则取消。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.tasks.billing_tasks import _expire_pending_orders_async
from backend.utils.time_utils import utc_now


class _Scalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _ExecResult:
    def __init__(self, *, scalars=(), one=None):
        self._scalars = list(scalars)
        self._one = one

    def scalars(self):
        return _Scalars(self._scalars)

    def scalar_one_or_none(self):
        return self._one


class _FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _query):
        assert self.results, "unexpected execute"
        return self.results.pop(0)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_never_fetched_order_cancelled_and_coupon_released(monkeypatch):
    order = SimpleNamespace(
        id="o-1",
        order_no="BJT-TEST-1",
        status="pending",
        external_order_no=None,
        coupon_id=77,
        expires_at=utc_now() - timedelta(minutes=5),
    )
    session = _FakeSession(
        [
            _ExecResult(scalars=["o-1"]),   # id 扫描
            _ExecResult(one=order),         # 订单行
        ]
    )

    released = []
    async def _release(coupon_id, order_no):
        released.append((coupon_id, order_no))

    async def _not_called(*args, **kwargs):
        raise AssertionError("未取码订单不应查交行")

    monkeypatch.setattr(
        "backend.services.operate_recharge.query_order_status", _not_called
    )
    monkeypatch.setattr(
        "backend.services.operate_coupons.release_coupon", _release
    )

    result = await _expire_pending_orders_async(lambda: session)

    assert result == {"completed": 0, "cancelled": 1, "skipped": 0, "errors": 0}
    assert order.status == "cancelled"
    assert released == [(77, "BJT-TEST-1")]


@pytest.mark.asyncio
async def test_fetched_order_paid_after_expiry_is_completed(monkeypatch):
    order = SimpleNamespace(
        id="o-2",
        order_no="BJT-TEST-2",
        status="pending",
        external_order_no="BANK-REF-2",
        coupon_id=None,
        user_id="u-1",
        expires_at=utc_now() - timedelta(hours=30),
    )
    user = SimpleNamespace(id="u-1")
    session = _FakeSession(
        [
            _ExecResult(scalars=["o-2"]),   # id 扫描
            _ExecResult(one=order),         # 订单行
            _ExecResult(one=user),          # complete 前拉 user
        ]
    )

    completed = []
    async def _query_bank(ref):
        return "success"

    async def _complete(db, u, o, *, allow_expired_if_paid):
        assert allow_expired_if_paid is True
        o.status = "completed"
        completed.append(o.order_no)

    monkeypatch.setattr(
        "backend.services.operate_recharge.query_order_status", _query_bank
    )
    monkeypatch.setattr("backend.services.billing.complete_order", _complete)

    result = await _expire_pending_orders_async(lambda: session)

    assert result == {"completed": 1, "cancelled": 0, "skipped": 0, "errors": 0}
    assert order.status == "completed"
    assert completed == ["BJT-TEST-2"]


@pytest.mark.asyncio
async def test_fetched_order_unpaid_expired_is_cancelled(monkeypatch):
    order = SimpleNamespace(
        id="o-3",
        order_no="BJT-TEST-3",
        status="pending",
        external_order_no="BANK-REF-3",
        coupon_id=9,
        expires_at=utc_now() - timedelta(days=3),
    )
    session = _FakeSession(
        [
            _ExecResult(scalars=["o-3"]),
            _ExecResult(one=order),
        ]
    )

    async def _query_bank(ref):
        return "pending"

    released = []
    async def _release(coupon_id, order_no):
        released.append(order_no)

    monkeypatch.setattr(
        "backend.services.operate_recharge.query_order_status", _query_bank
    )
    monkeypatch.setattr(
        "backend.services.operate_coupons.release_coupon", _release
    )

    result = await _expire_pending_orders_async(lambda: session)

    assert result == {"completed": 0, "cancelled": 1, "skipped": 0, "errors": 0}
    assert order.status == "cancelled"
    assert released == ["BJT-TEST-3"]
