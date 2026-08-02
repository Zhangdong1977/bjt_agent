"""集群 / 工作节点状态采集。

bjt-agent 是多节点集群（prod 3 节点），每节点跑若干 celery worker，worker
主机名形如 ``${NODE_NAME}_{review|parser}_${i}``（见 ``scripts/bjt-cluster.sh``）。
本模块用 ``celery_app.control.inspect()`` 实时探测：

- ``ping``   —— 谁还活着
- ``active`` —— 每个 worker 正在执行的任务（用来回答「该节点是否有在审/在解析任务」）
- ``stats``  —— 每个 worker 累计处理量

再把 worker 归并到节点，叠加可选的「期望节点清单」（``cluster_node_specs``）
让整节点掉线时也能显示 offline。所有探测都用 ``asyncio.to_thread`` 包起来
（celery inspect 是同步阻塞的），失败降级——绝不向上抛 500，状态页要能稳定打开。
"""

import asyncio
import json
import logging
import re
from typing import Any

from backend.celery_app import celery_app
from backend.config import get_settings

logger = logging.getLogger(__name__)

# worker 名 -> (node, role, idx)。先去掉 celery 可能追加的 '@host' 后缀。
_WORKER_RE = re.compile(r"^(?P<node>.+?)_(?P<role>review|parser)_(?P<idx>\d+)$")

_INSPECT_TIMEOUT = 2.0  # 秒；超过未应答视为该 worker 不在线


def _classify_task(task_name: str) -> str:
    """按任务全名归到 review / parser / other。"""
    if "review_tasks" in task_name:
        return "review"
    if "document_parser" in task_name:
        return "parser"
    return "other"


def _parse_worker_name(raw: str) -> tuple[str, str | None, int | None]:
    """返回 (节点名, role|None, idx|None)。不匹配集群命名约定的 -> standalone。"""
    name = raw.split("@", 1)[0]
    m = _WORKER_RE.match(name)
    if not m:
        return (name, None, None)
    return (m.group("node"), m.group("role"), int(m.group("idx")))


def _expected_nodes() -> list[dict[str, Any]]:
    """解析可选的 ``cluster_node_specs``（JSON env）。空 -> []。"""
    raw = get_settings().cluster_node_specs.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("name")]
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("cluster_node_specs 解析失败，忽略: %s", e)
    return []


# ---------------------------------------------------------------------------
# 同步 celery 探测（每个调用各建一个 inspect，互不共享连接）
# ---------------------------------------------------------------------------


def _ping() -> dict[str, Any]:
    try:
        return celery_app.control.inspect(timeout=_INSPECT_TIMEOUT).ping() or {}
    except Exception as e:  # broker 不可达等
        logger.warning("celery inspect.ping 失败: %s", e)
        return {}


def _active() -> dict[str, Any]:
    try:
        return celery_app.control.inspect(timeout=_INSPECT_TIMEOUT).active() or {}
    except Exception as e:
        logger.warning("celery inspect.active 失败: %s", e)
        return {}


def _stats() -> dict[str, Any]:
    try:
        return celery_app.control.inspect(timeout=_INSPECT_TIMEOUT).stats() or {}
    except Exception as e:
        logger.warning("celery inspect.stats 失败: %s", e)
        return {}


# ---------------------------------------------------------------------------
# 归并
# ---------------------------------------------------------------------------


def _build_workers(
    ping: dict[str, Any],
    active: dict[str, Any],
    stats: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    """把三个探测结果归并成 per-worker 列表，并返回 node->roles 映射。"""
    all_names = set(ping) | set(active) | set(stats)
    node_roles: dict[str, set[str]] = {}
    workers: list[dict[str, Any]] = []
    for raw_name in sorted(all_names):
        node, role, idx = _parse_worker_name(raw_name)
        tasks = active.get(raw_name) or []
        active_review = sum(
            1 for t in tasks if _classify_task(t.get("name", "")) == "review"
        )
        active_parser = sum(
            1 for t in tasks if _classify_task(t.get("name", "")) == "parser"
        )
        stat = stats.get(raw_name) or {}
        total = stat.get("total") or {}
        processed = sum(int(v) for v in total.values() if isinstance(v, (int, float)))
        if role:
            node_roles.setdefault(node, set()).add(role)
        workers.append(
            {
                "name": raw_name,
                "node": node,
                "role": role or "standalone",
                "index": idx,
                "alive": raw_name in ping,
                "active_review_tasks": active_review,
                "active_parser_tasks": active_parser,
                "processed": processed,
                "uptime": stat.get("uptime"),
            }
        )
    return workers, node_roles


def _group_nodes(
    workers: list[dict[str, Any]],
    node_roles: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """per-worker 列表 -> per-node 聚合，叠加期望节点清单（掉线节点也展示）。"""
    by_node: dict[str, dict[str, Any]] = {}

    def _ensure(node: str, label: str | None = None) -> dict[str, Any]:
        if node not in by_node:
            by_node[node] = {
                "name": node,
                "label": label or node,
                "roles": sorted(node_roles.get(node, set())),
                "alive_workers": 0,
                "total_workers": 0,
                "active_review_tasks": 0,
                "active_parser_tasks": 0,
                "processed": 0,
                "is_online": False,
            }
        return by_node[node]

    for w in workers:
        node = _ensure(w["node"])
        node["total_workers"] += 1
        if w["alive"]:
            node["alive_workers"] += 1
            node["is_online"] = True
        node["active_review_tasks"] += w["active_review_tasks"]
        node["active_parser_tasks"] += w["active_parser_tasks"]
        node["processed"] += w["processed"]
        # role 以实际探测到的为准（期望清单可能没填对）
        if w["role"] != "standalone":
            node["roles"] = sorted(set(node["roles"]) | {w["role"]})

    # 叠加期望节点（来自 cluster_node_specs），让整节点掉线也能显示
    for spec in _expected_nodes():
        node = _ensure(spec["name"], label=spec.get("label"))
        if spec.get("roles"):
            node["roles"] = sorted(set(node["roles"]) | set(spec["roles"]))

    return sorted(by_node.values(), key=lambda n: n["name"])


def _queue_depths() -> dict[str, int | None]:
    """review / parser 队列长度（Redis LLEN）。broker 不可达 -> None。"""
    broker = get_settings().celery_broker_url
    if not broker:
        return {"review": None, "parser": None}
    try:
        import redis as redis_lib

        r = redis_lib.from_url(broker, decode_responses=True, socket_timeout=2.0)
        return {
            "review": int(r.llen("review")),
            "parser": int(r.llen("parser")),
        }
    except Exception as e:
        logger.warning("读取 celery 队列深度失败: %s", e)
        return {"review": None, "parser": None}


async def _probe_cluster_status() -> dict[str, Any]:
    """真实采集集群实时状态（节点 + worker + 队列深度）。永不抛异常。

    本函数直接发 celery inspect 广播，单次约 2s（inspect 等满 timeout）。
    高频调用方应走 ``get_cluster_status``（带 Redis 共享缓存），不要直调本函数。
    """
    ping, active, stats = await asyncio.gather(
        asyncio.to_thread(_ping),
        asyncio.to_thread(_active),
        asyncio.to_thread(_stats),
    )
    workers, node_roles = _build_workers(ping, active, stats)
    nodes = _group_nodes(workers, node_roles)
    queue = await asyncio.to_thread(_queue_depths)

    alive = sum(1 for w in workers if w["alive"])
    degraded = not (ping or active or stats)  # 三个探测全空 -> broker 可能不可达

    return {
        "nodes": nodes,
        "workers": workers,
        "queue_depths": queue,
        "alive_workers": alive,
        "total_workers": len(workers),
        "degraded": degraded,
    }


# ---------------------------------------------------------------------------
# Redis 共享缓存层
#
# celery ``control.inspect()`` 即便所有 worker 瞬间回完也会等满 ``timeout``
# （实测：9 worker 在 timeout=0.3s 即收齐，写死 2.0s 则必等满 2s）。状态页
# 接口每次调用都发 3 次 inspect → 单请求 ≈2s。前端 5s 高频轮询 + 多 tab/刷新
# 时并发请求占满 gunicorn worker → nginx upstream 排队 → 前端断开（499）。
#
# 本层把"实时探测结果"按 TTL 缓存到全集群共用的 Redis（``settings.redis_url``，
# 与 ``_queue_depths`` 同源）。TTL 内所有调用（含 3 节点 × 多 worker）共享一份
# 缓存，真实 inspect 每 TTL 最多发一次。Redis 不可达时全部降级为直连探测，
# 绝不让缓存层拖垮状态页。
# ---------------------------------------------------------------------------

# 固定 2 个 key（不随请求累积），带 TTL 自动回收，详见 get_cluster_status 文档。
_CACHE_KEY = "cluster:status:v1"        # 探测结果 JSON
_CACHE_LOCK_KEY = "cluster:status:lock"  # stampede 防护：持锁者探测，其余等结果
_CACHE_TTL = 8                          # 秒；> 前端轮询周期(5s) 使大部分命中
_CACHE_LOCK_TTL = 15                    # 秒；略大于单次探测最坏耗时，崩漏时 TTL 兜底
_CACHE_WAIT = 0.3                       # 秒；非持锁者重试间隔


def _redis_client():
    """从 settings.redis_url 建短生命 redis 连接（与 _queue_depths 同范式）。

    返回 None 表示未配置 / 不可用 —— 调用方据此降级。
    """
    url = get_settings().redis_url
    if not url:
        return None
    try:
        import redis as redis_lib

        return redis_lib.from_url(url, decode_responses=True, socket_timeout=2.0)
    except Exception as e:
        logger.warning("cluster_status 缓存 redis 初始化失败，降级直连探测: %s", e)
        return None


def _cache_get() -> dict[str, Any] | None:
    """读缓存。Redis 不可达 / key 不存在 / JSON 损坏 → 返回 None（放行真实探测）。"""
    r = _redis_client()
    if r is None:
        return None
    try:
        raw = r.get(_CACHE_KEY)
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning("cluster_status 读缓存失败，放行真实探测: %s", e)
        return None


def _cache_set(payload: dict[str, Any]) -> None:
    """写缓存（带 TTL）。失败仅 warn，不影响调用方拿到的结果。"""
    r = _redis_client()
    if r is None:
        return
    try:
        r.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(payload, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning("cluster_status 写缓存失败（不影响本次结果）: %s", e)


def _acquire_probe_lock() -> bool:
    """抢探测锁（SET NX EX）。True=我方持锁去探测，False=他人正在探测。"""
    r = _redis_client()
    if r is None:
        return True  # Redis 不可达 → 不参与互斥，调用方直接探测
    try:
        return bool(r.set(_CACHE_LOCK_KEY, "1", nx=True, ex=_CACHE_LOCK_TTL))
    except Exception as e:
        logger.warning("cluster_status 抢锁失败，放行直连探测: %s", e)
        return True


def _release_probe_lock() -> None:
    """释放探测锁。失败仅 warn（TTL 兜底自动回收）。"""
    r = _redis_client()
    if r is None:
        return
    try:
        r.delete(_CACHE_LOCK_KEY)
    except Exception as e:
        logger.warning("cluster_status 释放锁失败（TTL 会兜底）: %s", e)


async def get_cluster_status() -> dict[str, Any]:
    """采集集群实时状态（带 Redis 共享缓存，TTL 内全集群共享一份）。

    缓存策略：
    - 命中缓存（TTL 8s 内）→ 直接返回，~ms 级。
    - 未命中：第一个调用抢锁做真实探测并写回，其余调用轮询等结果
      （stampede 防护，避免多节点并发各自发一遍 inspect）。
    - Redis 不可达 → 全部降级为直连 ``_probe_cluster_status``，行为同改前。

    内存占用：固定 2 个 Redis key（结果 + 锁），均带 TTL 自动回收，不累积。
    """
    cached = _cache_get()
    if cached is not None:
        return cached

    if not _acquire_probe_lock():
        # 未抢到锁：等持锁者写回，最多等到锁 TTL
        deadline_steps = int(_CACHE_LOCK_TTL / _CACHE_WAIT)
        for _ in range(deadline_steps):
            await asyncio.sleep(_CACHE_WAIT)
            cached = _cache_get()
            if cached is not None:
                return cached
        # 持锁者超时未回写（崩溃 / 探测极慢）—— 兜底直连，绝不阻塞调用方
        logger.warning("cluster_status 等待缓存超时，降级直连探测")

    try:
        result = await _probe_cluster_status()
        _cache_set(result)
    finally:
        _release_probe_lock()
    return result
