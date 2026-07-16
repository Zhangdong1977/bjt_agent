"""订单 / 消费列表的内部用户全站可见性测试。

外部用户只能看到自己的订单与消费；内部用户（interior_user=True，来自签名
JWT claim）可看全站数据，并附带归属（username / enterprise_name），且支持
按 username / enterprise_name 筛选。钱包、支付等写动作接口不在本测试范围
（内部用户只放开只读列表，遵循"读开放、写仍锁定"约定）。
"""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.api.deps import create_access_token, get_password_hash
from backend.models import (
    BillingOrder,
    ConsumptionRecord,
    User,
    async_session_factory,
    engine,
)
from backend.utils.time_utils import utc_now

API = "/api/billing"

# 每次运行一个唯一前缀：username 跨次运行唯一（避免冲突），同一 run 内多个测试
# 复用同一用户记录（_make_user 是 get-or-create）；order_no / task_id 等唯一约束
# 字段同样拼上 RUN_ID，保证可重复运行不撞唯一键。
RUN_ID = uuid.uuid4().hex[:8]


def _un(name: str) -> str:
    """用户名加运行前缀，保证跨次运行唯一、同 run 内稳定可筛选。"""
    return f"{name}_{RUN_ID}"


def _prod(tag: str) -> str:
    """产品名加运行前缀：内部用户看全站时，断言只命中本次 run 的数据，
    不被历史/其他测试残留数据干扰。"""
    return f"套餐{tag}_{RUN_ID}"


def _proj(tag: str) -> str:
    """项目名加运行前缀（同 _prod 理由）。"""
    return f"项目{tag}_{RUN_ID}"


def _order_no() -> str:
    """每次生成唯一订单号（UUID），避免跨测试方法、跨运行的唯一键冲突。"""
    return f"TEST-ORDER-{uuid.uuid4().hex[:12]}"


def _uuid() -> str:
    """生成标准 36 字符 UUID（满足 task_id / project_id 的 String(36) 约束）。"""
    return str(uuid.uuid4())


async def _make_user(username: str, *, enterprise: str | None = None) -> User:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=username,
                email=f"{username}@example.com",
                password_hash=get_password_hash("Test123!"),
                enterprise_name=enterprise,
            )
            session.add(user)
            await session.flush()
            await session.refresh(user)
        await session.commit()
    return user


async def _make_order(user: User, *, product_name: str, seed: str) -> BillingOrder:
    """直接构造一条已完成订单（绕过支付流程）。order_no 随机唯一，seed 不参与唯一性。"""
    del seed
    async with async_session_factory() as session:
        order = BillingOrder(
            order_no=_order_no(),
            user_id=user.id,
            product_code="basic",
            product_name=product_name,
            status="completed",
            order_amount_cents=10000,
            actual_payment_cents=10000,
            package_balance_wen=1200,
            coupon_amount_cents=0,
            points_used=0,
            points_amount_cents=0,
            expires_at=utc_now() + timedelta(days=1),
            paid_at=utc_now(),
            balance_after_wen=1200,
        )
        session.add(order)
        await session.commit()
    return order


async def _make_consumption(user: User, *, project_name: str, seed: str) -> ConsumptionRecord:
    del seed  # task_id/project_id 用随机 UUID 保证唯一，无需 seed 关联
    async with async_session_factory() as session:
        record = ConsumptionRecord(
            user_id=user.id,
            task_id=_uuid(),
            project_id=_uuid(),
            project_name=project_name,
            consumed_wen=10,
            earned_points=0,
            used_by=user.username,
            cost_cny=0.25,
            balance_after_wen=1190,
        )
        session.add(record)
        await session.commit()
    return record


async def _token_for(user: User, *, interior: bool) -> str:
    await engine.dispose()
    return create_access_token(
        data={"sub": user.id, "interior_user": interior, "concurrency": 2}
    )


@pytest.mark.asyncio
class TestBillingOrderVisibility:
    async def test_external_user_sees_only_own_orders(
        self, client: AsyncClient
    ):
        owner_a = await _make_user(_un("bill_owner_a"), enterprise="甲公司")
        owner_b = await _make_user(_un("bill_owner_b"), enterprise="乙公司")
        await _make_order(owner_a, product_name=_prod("A"), seed="a")
        await _make_order(owner_b, product_name=_prod("B"), seed="b")

        token = await _token_for(owner_a, interior=False)
        resp = await client.get(
            f"{API}/orders", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        orders = resp.json()["orders"]
        product_names = {o["product_name"] for o in orders}
        assert _prod("A") in product_names
        assert _prod("B") not in product_names
        # 外部用户只看自己的：归属回填为自己（JOIN 是统一的，不对外部泄露他人信息）
        own_orders = [o for o in orders if o["product_name"] == _prod("A")]
        assert own_orders, "应至少有一条自己的订单"
        assert all(o["username"] == _un("bill_owner_a") for o in own_orders)

    async def test_interior_user_sees_all_orders_with_ownership(
        self, client: AsyncClient
    ):
        owner_a = await _make_user(_un("bill_owner_a"), enterprise="甲公司")
        owner_b = await _make_user(_un("bill_owner_b"), enterprise="乙公司")
        await _make_order(owner_a, product_name=_prod("A"), seed="a")
        await _make_order(owner_b, product_name=_prod("B"), seed="b")

        interior = await _make_user(_un("bill_interior_a"))
        token = await _token_for(interior, interior=True)
        resp = await client.get(
            f"{API}/orders", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        orders = resp.json()["orders"]
        product_names = {o["product_name"] for o in orders}
        assert {_prod("A"), _prod("B")}.issubset(product_names)
        # 内部用户视角应回填归属
        by_product = {o["product_name"]: o for o in orders}
        assert by_product[_prod("A")]["username"] == _un("bill_owner_a")
        assert by_product[_prod("A")]["enterprise_name"] == "甲公司"
        assert by_product[_prod("B")]["username"] == _un("bill_owner_b")

    async def test_interior_user_can_filter_by_username(self, client: AsyncClient):
        owner_a = await _make_user(_un("bill_owner_a"), enterprise="甲公司")
        owner_b = await _make_user(_un("bill_owner_b"), enterprise="乙公司")
        await _make_order(owner_a, product_name=_prod("A"), seed="a")
        await _make_order(owner_b, product_name=_prod("B"), seed="b")

        interior = await _make_user(_un("bill_interior_a"))
        token = await _token_for(interior, interior=True)
        resp = await client.get(
            f"{API}/orders?username={_un('bill_owner_b')}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        orders = resp.json()["orders"]
        # 仅本次 run 的 owner_b 订单（RUN_ID 唯一，不会误匹配历史数据）
        own_b_orders = {
            o["product_name"] for o in orders if o["product_name"].startswith("套餐B_")
        }
        assert own_b_orders == {_prod("B")}

    async def test_external_user_username_filter_ignored(self, client: AsyncClient):
        """外部用户即便传了 username 筛选，也仍被 user_id 锁定到自己。"""
        owner_a = await _make_user(_un("bill_owner_a"), enterprise="甲公司")
        owner_b = await _make_user(_un("bill_owner_b"), enterprise="乙公司")
        await _make_order(owner_a, product_name=_prod("A"), seed="a")
        await _make_order(owner_b, product_name=_prod("B"), seed="b")

        token = await _token_for(owner_a, interior=False)
        resp = await client.get(
            f"{API}/orders?username={_un('bill_owner_b')}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        own_a_orders = {
            o["product_name"]
            for o in resp.json()["orders"]
            if o["product_name"].startswith("套餐A_")
        }
        assert own_a_orders == {_prod("A")}


@pytest.mark.asyncio
class TestBillingConsumptionVisibility:
    async def test_external_user_sees_only_own_consumptions(
        self, client: AsyncClient
    ):
        owner_a = await _make_user(_un("bill_owner_a"), enterprise="甲公司")
        owner_b = await _make_user(_un("bill_owner_b"), enterprise="乙公司")
        await _make_consumption(owner_a, project_name=_proj("A"), seed="a")
        await _make_consumption(owner_b, project_name=_proj("B"), seed="b")

        token = await _token_for(owner_a, interior=False)
        resp = await client.get(
            f"{API}/consumptions", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        names = {c["project_name"] for c in resp.json()["consumptions"]}
        assert _proj("A") in names
        assert _proj("B") not in names
        # 外部用户只看自己的：归属回填为自己
        own = [c for c in resp.json()["consumptions"] if c["project_name"] == _proj("A")]
        assert own, "应至少有一条自己的消费记录"
        assert all(c["username"] == _un("bill_owner_a") for c in own)

    async def test_interior_user_sees_all_consumptions_with_ownership(
        self, client: AsyncClient
    ):
        owner_a = await _make_user(_un("bill_owner_a"), enterprise="甲公司")
        owner_b = await _make_user(_un("bill_owner_b"), enterprise="乙公司")
        await _make_consumption(owner_a, project_name=_proj("A"), seed="a")
        await _make_consumption(owner_b, project_name=_proj("B"), seed="b")

        interior = await _make_user(_un("bill_interior_a"))
        token = await _token_for(interior, interior=True)
        resp = await client.get(
            f"{API}/consumptions", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        by_project = {c["project_name"]: c for c in resp.json()["consumptions"]}
        assert {_proj("A"), _proj("B")}.issubset(by_project.keys())
        assert by_project[_proj("A")]["username"] == _un("bill_owner_a")
        assert by_project[_proj("A")]["enterprise_name"] == "甲公司"
        assert by_project[_proj("B")]["username"] == _un("bill_owner_b")
