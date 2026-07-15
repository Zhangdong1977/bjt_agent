"""throttled_save 单元测试：验证分块限速的正确性。

覆盖：
- THROTTLE-001: bytes_per_sec=0（不限速）时立即写完、内容正确
- THROTTLE-002: 限速时总耗时 ≈ 数据量/速率（时间补偿生效），内容正确
- THROTTLE-003: 多 chunk 边界（数据量非 chunk 整数倍）写完整
"""

import io
import time
from pathlib import Path

import pytest
from fastapi import UploadFile

from backend.middleware.upload_throttle import throttled_save


def _make_upload(data: bytes) -> UploadFile:
    return UploadFile(filename="test.pdf", file=io.BytesIO(data))


@pytest.mark.asyncio
async def test_throttle_unlimited_writes_immediately(tmp_path: Path):
    """THROTTLE-001: 不限速时应立即写完，内容与字节数正确。"""
    data = b"hello throttle " * 1000  # ~15KB
    dest = tmp_path / "unlimited.bin"

    start = time.monotonic()
    written = await throttled_save(_make_upload(data), dest, bytes_per_sec=0)
    elapsed = time.monotonic() - start

    assert written == len(data)
    assert dest.read_bytes() == data
    # 不限速：15KB 写盘应远低于 1s
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_throttle_enforces_rate(tmp_path: Path):
    """THROTTLE-002: 限速时总耗时 ≈ 数据量/速率。

    256KB @ 1MB/s ≈ 0.25s。断言下限留 20% 余量，上限放宽防 CI 抖动。
    """
    data = b"x" * (256 * 1024)
    dest = tmp_path / "throttled.bin"
    rate = 1024 * 1024  # 1 MB/s

    start = time.monotonic()
    written = await throttled_save(_make_upload(data), dest, bytes_per_sec=rate)
    elapsed = time.monotonic() - start

    assert written == len(data)
    assert dest.read_bytes() == data
    expected = len(data) / rate
    assert elapsed >= expected * 0.8, f"限速未生效：耗时 {elapsed:.3f}s 低于预期 {expected:.3f}s"
    assert elapsed < expected + 2.0, f"限速过慢：耗时 {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_throttle_non_aligned_chunks(tmp_path: Path):
    """THROTTLE-003: 数据量非 64KB 整数倍时仍写完整（末尾不足一块）。"""
    # 100KB = 1×64KB + 1×36KB
    data = bytes(range(256)) * 400  # 100000 bytes
    dest = tmp_path / "unaligned.bin"

    written = await throttled_save(_make_upload(data), dest, bytes_per_sec=0)

    assert written == len(data)
    assert dest.read_bytes() == data
