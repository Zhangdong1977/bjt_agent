"""get_db_session 连接泄漏防回归测试。

生产根因：``get_db_session`` 曾用 ``except Exception``，接不住客户端取消请求时
Starlette 抛入协程的 ``asyncio.CancelledError``（``BaseException`` 子类），导致
``rollback()`` 被跳过、连接以 ``idle in transaction`` 状态泄漏。状态页高频轮询
几小时即堆出几十个泄漏连接、占满连接池（81/100）→ 全站文档上传 500。

本测试锁定「取消路径必须 rollback」这一行为，防止有人把 ``except BaseException``
「清理」回 ``except Exception``。

运行：D:\\miniconda3\\envs\\bjt-agent\\python.exe -m pytest backend/tests/test_db_session_cancel.py -v
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models import base as base_module

pytestmark = pytest.mark.asyncio


def _patched_factory(fake_session: MagicMock) -> MagicMock:
    """构造替身 factory：``async with factory() as session`` -> session=fake_session。"""
    async_cm = AsyncMock()
    async_cm.__aenter__.return_value = fake_session
    return MagicMock(return_value=async_cm)


def _fake_session() -> MagicMock:
    """普通 MagicMock + 显式 AsyncMock 的 DB 方法，避免 AsyncMock 子属性歧义。"""
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


async def test_get_db_session_rolls_back_on_cancellation():
    """向生成器内抛 CancelledError（模拟客户端取消请求）时必须触发 rollback。"""
    session = _fake_session()

    with patch.object(base_module, "async_session_factory", _patched_factory(session)):
        gen = base_module.get_db_session()
        yielded = await gen.__anext__()  # 推进到 yield
        assert yielded is session

        # 模拟客户端取消请求：向生成器内抛 CancelledError
        with pytest.raises(asyncio.CancelledError):
            await gen.athrow(asyncio.CancelledError())

    # 核心断言：取消路径必须 rollback；否则事务残留 -> idle in transaction 泄漏。
    session.rollback.assert_awaited()
    session.close.assert_awaited()
    session.commit.assert_not_awaited()


async def test_get_db_session_commits_on_normal_completion():
    """正常完成（框架在请求结束后再次推进生成器越过 yield）走 commit。

    回归基线：确保捕获 BaseException 的改动没有把正常路径也带偏到 rollback。
    """
    session = _fake_session()

    with patch.object(base_module, "async_session_factory", _patched_factory(session)):
        gen = base_module.get_db_session()
        await gen.__anext__()  # 走到 yield
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()  # 越过 yield -> commit -> return

    session.commit.assert_awaited()
    session.close.assert_awaited()
    session.rollback.assert_not_awaited()
