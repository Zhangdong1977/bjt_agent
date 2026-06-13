"""Celery worker 日志配置 —— 进程安全的滚动文件 handler。

设计要点：
- 通过连接 Celery 的 ``setup_logging`` 信号接管 worker 日志，使用
  ``concurrent_log_handler.ConcurrentRotatingFileHandler`` 写入 ``--logfile``
  指定的文件（默认 50MB × 5 份滚动备份，单 worker 上限约 300MB）。
- 为何不用 stdlib ``RotatingFileHandler``：Celery prefork 在 ``--concurrency=N``
  下，每个 fork 出的子进程都会重新执行日志配置，N 个进程各自持有一个 handler
  写同一文件，轮转时会互相覆盖备份/丢行。``ConcurrentRotatingFileHandler``
  用文件锁串行化写入与轮转，是多进程下的正确选择。
- 抑制高频噪声 logger（httpx/httpcore/openai 的 HTTP 请求日志）。
- 子代理 logger（``sub_agent``）关闭向 root 传播，避免 DEBUG 行重复写入主日志。
"""

import logging
import logging.config
from pathlib import Path

from celery.signals import setup_logging

# 滚动备份参数（与 backend.log 保持一致）
_MAX_BYTES = 50 * 1024 * 1024   # 50 MB
_BACKUP_COUNT = 5               # 保留 5 个历史备份

# 标准日志格式
_FORMATTER = {
    "standard": {
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    }
}


@setup_logging.connect
def _configure_worker_logging(logfile=None, loglevel=None, **kwargs):
    """接管 Celery worker 日志：写入滚动文件或回退到 stderr。

    Celery 通过 ``--logfile``/``--loglevel`` 把以下关键字参数传入本信号：
    - ``logfile``: 文件路径字符串，或 ``None``（未指定 ``--logfile`` 时）。
    - ``loglevel``: **int**（Celery 内部已用 ``mlevel`` 转换为数字级别），
      或 ``None``。
    一旦连接了本信号，Celery 会跳过自身的默认日志配置，完全交由本函数接管。
    """
    # loglevel 是 int（如 20=INFO）；None 时默认 INFO
    if isinstance(loglevel, int):
        level = logging.getLevelName(loglevel)
    elif isinstance(loglevel, str) and loglevel:
        level = loglevel.upper()
    else:
        level = "INFO"

    if logfile:
        # 确保日志目录存在
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        handlers = {
            "rotating": {
                "class": "concurrent_log_handler.ConcurrentRotatingFileHandler",
                "filename": logfile,
                "maxBytes": _MAX_BYTES,
                "backupCount": _BACKUP_COUNT,
                "formatter": "standard",
                "encoding": "utf-8",
            }
        }
        root_handlers = ["rotating"]
    else:
        # 兜底：未指定 --logfile 时（如手动 celery worker），输出到 stderr
        handlers = {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "stream": "ext://sys.stderr",
            }
        }
        root_handlers = ["console"]

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": _FORMATTER,
        "handlers": handlers,
        "root": {
            "handlers": root_handlers,
            "level": level,
        },
        "loggers": {
            # 高频 HTTP 请求日志，仅保留 WARNING 及以上
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
            "openai": {"level": "WARNING"},
            # 子代理 logger 自有 per-todo 文件 handler，不向 root 传播以免重复
            "sub_agent": {"propagate": False},
        },
    })
