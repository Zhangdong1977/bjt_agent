"""上传限速工具：基于分块流式读取 + 时间补偿的单连接带宽限制。

为什么放在后端而不是 nginx？
    nginx 的 ``limit_rate`` 只能限制「响应/下载」速率，对「上传请求体」无效，
    且 nginx 没有内置的上传限速指令。本项目 nginx 已设 ``proxy_request_buffering off``
    （让前端 XHR 上传进度条可靠），上传字节流直通后端；此时后端读得慢，TCP
    滑动窗口会收缩并反压到浏览器，浏览器自动减速——因此「后端限速」即可
    达成端到端限速，且集群所有节点（应用层）行为一致。

算法：时间补偿，而非固定 sleep。
    按累计已写字节计算理论耗时 ``written / bytes_per_sec``，当实际耗时落后于
    理论耗时才 ``await asyncio.sleep`` 补齐。这样平均速率稳定，且不限速
    （``bytes_per_sec == 0``）时零开销。

注意：必须在协程里用 ``await upload.read()`` 与 ``await asyncio.sleep``，
    绝不能 ``time.sleep``，否则慢速上传会阻塞整个事件循环。
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import UploadFile

logger = logging.getLogger(__name__)

# 分块大小：64KB。读/写都以该粒度进行，64KB 同步写盘 <1ms，事件循环可容忍。
CHUNK_SIZE = 64 * 1024


async def throttled_save(
    upload: UploadFile,
    dest: Path,
    *,
    bytes_per_sec: int,
    fsync: bool = False,
) -> int:
    """流式读取 ``upload`` 并限速写入 ``dest``，返回写入字节数。

    Args:
        upload: FastAPI UploadFile，用 ``await upload.read()`` 异步读取。
        dest: 目标文件路径（父目录需已存在或由调用方创建）。
        bytes_per_sec: 目标速率（字节/秒）；``0`` 或负值表示不限速。
        fsync: 写完后是否 ``fsync`` 强制刷盘。NFS 等共享存储场景下，
            确保上传字节已传播到 server，避免「parser 跨节点读不到」的竞态。

    限速语义：该速率约束的是「本连接读取并落盘」的平均速率。因 nginx 不缓冲
    请求体，等价于约束整条上传链路的速率。
    """
    written = 0
    start = time.monotonic()
    with dest.open("wb") as buffer:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            buffer.write(chunk)
            written += len(chunk)

            if bytes_per_sec > 0:
                expected = written / bytes_per_sec
                lag = expected - (time.monotonic() - start)
                if lag > 0:
                    await asyncio.sleep(lag)

        buffer.flush()
        if fsync:
            os.fsync(buffer.fileno())

    logger.debug(
        "[UploadThrottle] saved %s bytes=%d rate=%dB/s elapsed=%.2fs",
        dest.name,
        written,
        bytes_per_sec,
        time.monotonic() - start,
    )
    return written
