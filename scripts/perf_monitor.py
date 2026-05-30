#!/usr/bin/env python3
"""Performance monitor for bid review tasks.

Collects real-time metrics during task execution to validate the cluster
deployment plan's resource estimates.

Usage:
    # Terminal 1: Start monitoring
    python scripts/perf_monitor.py start

    # Terminal 2: Run your bid review task via frontend

    # Terminal 1: Ctrl+C to stop and generate report

    # Or run for a fixed duration:
    python scripts/perf_monitor.py start --duration 1800   # 30 min

    # Generate report from a previous session:
    python scripts/perf_monitor.py report perf_data_20260526_143000.json

Metrics collected every 5 seconds:
    - Celery worker process memory (RSS) and CPU
    - PostgreSQL active connections
    - Redis memory, connections, and key counts
    - LLM call metrics (from Redis counters written by agent)
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psutil

# --- Configuration (loaded from backend/.env) ---
_env_file = PROJECT_ROOT / "backend" / ".env"


def _load_env(key: str) -> str:
    """Read a value from backend/.env file."""
    if not _env_file.exists():
        return ""
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _parse_db_url(url: str) -> dict:
    """Parse postgresql+asyncpg://user:pass@host:port/dbname."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "dbname": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
    }


def _parse_redis_url(url: str) -> dict:
    """Parse redis://host:port/db."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 6379,
        "db": int(parsed.path.lstrip("/") or 0),
    }


_db_config = _parse_db_url(_load_env("DATABASE_URL"))
_redis_config = _parse_redis_url(_load_env("REDIS_URL"))

SAMPLE_INTERVAL = 5  # seconds


def get_db_connections() -> dict:
    """Query PostgreSQL for active connection count."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=_db_config["host"], port=_db_config["port"],
            dbname=_db_config["dbname"],
            user=_db_config["user"], password=_db_config["password"],
            connect_timeout=3,
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT state, count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() GROUP BY state"
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        states = {r[0]: r[1] for r in rows}
        return {
            "total": sum(states.values()),
            "active": states.get("active", 0),
            "idle": states.get("idle", 0),
            "idle_in_transaction": states.get("idle in transaction", 0),
        }
    except Exception as e:
        return {"error": str(e)}


def get_redis_info() -> dict:
    """Query Redis for memory, connections, and relevant keys."""
    try:
        import redis
        r = redis.Redis(host=_redis_config["host"], port=_redis_config["port"],
                        db=_redis_config["db"], decode_responses=True,
                        socket_timeout=3, socket_connect_timeout=3)
        info = r.info()
        keyspace = r.info("keyspace") if r.dbsize() > 0 else {}

        # Count relevant keys
        sse_streams = len(r.keys("sse:stream:*"))
        stream_tasks = len(r.keys("stream:task:*"))
        llm_limit_keys = len(r.keys("llm_concurrent:*"))
        metrics_keys = len(r.keys("metrics:*"))

        result = {
            "used_memory_mb": round(info.get("used_memory", 0) / 1024 / 1024, 1),
            "used_memory_peak_mb": round(info.get("used_memory_peak", 0) / 1024 / 1024, 1),
            "connected_clients": info.get("connected_clients", 0),
            "total_connections_received": info.get("total_connections_received", 0),
            "total_commands_processed": info.get("total_commands_processed", 0),
            "keys": info.get("db0", {}).get("keys", r.dbsize()),
            "sse_streams": sse_streams,
            "stream_tasks": stream_tasks,
            "llm_limit_keys": llm_limit_keys,
            "metrics_keys": metrics_keys,
        }
        r.close()
        return result
    except Exception as e:
        return {"error": str(e)}


def get_llm_metrics() -> dict:
    """Read LLM call metrics written by agent instrumentation."""
    try:
        import redis
        r = redis.Redis(host=_redis_config["host"], port=_redis_config["port"],
                        db=_redis_config["db"], decode_responses=True,
                        socket_timeout=3, socket_connect_timeout=3)
        data = {}
        for key in r.keys("metrics:llm:*"):
            raw = r.get(key)
            if raw:
                data[key] = json.loads(raw)
        # Also get aggregate counters
        for key in r.keys("metrics:task:*"):
            raw = r.get(key)
            if raw:
                data[key] = json.loads(raw)
        r.close()
        return data
    except Exception as e:
        return {"error": str(e)}


def get_celery_workers() -> dict:
    """Collect memory and CPU of Celery worker processes."""
    workers = {"review": [], "parser": [], "other": []}

    # First pass: identify all celery PIDs
    celery_procs = {}
    for proc in psutil.process_iter(["pid", "cmdline", "memory_info", "cpu_percent",
                                      "create_time", "status", "num_threads", "ppid"]):
        try:
            cmdline = proc.info["cmdline"]
            if not cmdline:
                continue
            cmd_str = " ".join(cmdline)
            if "celery" not in cmd_str or "celery_app" not in cmd_str:
                continue
            celery_procs[proc.info["pid"]] = proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Second pass: build entries
    for proc in celery_procs.values():
        try:
            cmdline = proc.info["cmdline"]
            cmd_str = " ".join(cmdline)
            mem_mb = round(proc.info["memory_info"].rss / 1024 / 1024, 1)
            cpu = proc.cpu_percent(interval=None)

            # Determine queue from cmdline
            queue = "other"
            if "-Q review" in cmd_str:
                queue = "review"
            elif "-Q parser" in cmd_str:
                queue = "parser"

            # Distinguish main vs child by checking if parent is also a celery process
            is_child = proc.info["ppid"] in celery_procs

            entry = {
                "pid": proc.info["pid"],
                "memory_mb": mem_mb,
                "cpu_percent": cpu,
                "num_threads": proc.info["num_threads"],
                "status": proc.info["status"],
            }

            # Distinguish main vs child by parent relationship
            entry["role"] = "child" if is_child else "main"

            workers[queue].append(entry)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return workers


def get_system_overview() -> dict:
    """Get overall system resource usage."""
    mem = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "memory_total_gb": round(mem.total / 1024 / 1024 / 1024, 1),
        "memory_used_gb": round(mem.used / 1024 / 1024 / 1024, 1),
        "memory_percent": mem.percent,
    }


def collect_sample(sample_idx: int) -> dict:
    """Collect one round of all metrics."""
    ts = datetime.now().isoformat()
    return {
        "timestamp": ts,
        "elapsed_sec": sample_idx * SAMPLE_INTERVAL,
        "system": get_system_overview(),
        "workers": get_celery_workers(),
        "db": get_db_connections(),
        "redis": get_redis_info(),
        "llm_metrics": get_llm_metrics(),
    }


def generate_report(samples: list) -> str:
    """Generate a human-readable performance report from collected samples."""
    if not samples:
        return "No samples collected."

    lines = []
    lines.append("=" * 70)
    lines.append("  性能采集报告 - Bid Review Task Performance Report")
    lines.append("=" * 70)
    lines.append(f"采集时间: {samples[0]['timestamp']} ~ {samples[-1]['timestamp']}")
    lines.append(f"采集间隔: {SAMPLE_INTERVAL}s, 共 {len(samples)} 个样本")
    lines.append(f"总时长: {samples[-1]['elapsed_sec']}s ({round(samples[-1]['elapsed_sec']/60, 1)} min)")
    lines.append("")

    # --- System ---
    sys_data = [s["system"] for s in samples]
    lines.append("--- 1. 系统资源 ---")
    lines.append(f"  CPU 核心: {sys_data[0]['cpu_count']}")
    cpu_vals = [s["cpu_percent"] for s in sys_data]
    lines.append(f"  CPU 使用率: avg={round(sum(cpu_vals)/len(cpu_vals),1)}%, "
                 f"max={max(cpu_vals)}%")
    mem_vals = [s["memory_percent"] for s in sys_data]
    lines.append(f"  内存: {sys_data[0]['memory_total_gb']}GB 总量, "
                 f"使用 avg={round(sum(mem_vals)/len(mem_vals),1)}%, max={max(mem_vals)}%")
    lines.append("")

    # --- Worker Memory ---
    lines.append("--- 2. Celery Worker 内存 ---")
    for queue_name in ["review", "parser"]:
        all_procs = []
        for s in samples:
            all_procs.extend(s["workers"].get(queue_name, []))

        if not all_procs:
            lines.append(f"  [{queue_name}] 无活跃进程")
            continue

        main_procs = [p for p in all_procs if p["role"] == "main"]
        child_procs = [p for p in all_procs if p["role"] == "child"]

        if child_procs:
            mem_vals = [p["memory_mb"] for p in child_procs]
            lines.append(f"  [{queue_name}] Worker 子进程:")
            lines.append(f"    进程数: {len(set(p['pid'] for p in child_procs))}")
            lines.append(f"    内存 RSS: avg={round(sum(mem_vals)/len(mem_vals))}MB, "
                         f"max={max(mem_vals)}MB, "
                         f"peak (跨所有样本)={max(p['memory_mb'] for p in child_procs)}MB")

        # Peak memory per unique PID
        pid_peak = {}
        for p in all_procs:
            pid_peak[p["pid"]] = max(pid_peak.get(p["pid"], 0), p["memory_mb"])

        if pid_peak:
            total_peak = round(sum(pid_peak.values()))
            lines.append(f"    总峰值内存 (所有进程): {total_peak}MB ({round(total_peak/1024, 1)}GB)")

        # Memory growth trend
        if len(samples) > 2:
            first_avg = round(sum(p["memory_mb"] for p in
                                  [proc for proc in samples[0]["workers"].get(queue_name, [])
                                   if proc["role"] == "child"]) /
                              max(1, len([p for p in samples[0]["workers"].get(queue_name, [])
                                          if p["role"] == "child"])))
            last_avg = round(sum(p["memory_mb"] for p in
                                 [proc for proc in samples[-1]["workers"].get(queue_name, [])
                                  if proc["role"] == "child"]) /
                             max(1, len([p for p in samples[-1]["workers"].get(queue_name, [])
                                         if p["role"] == "child"])))
            lines.append(f"    内存增长: {first_avg}MB → {last_avg}MB "
                         f"(+{last_avg - first_avg}MB)")
    lines.append("")

    # --- DB Connections ---
    lines.append("--- 3. PostgreSQL 连接 ---")
    db_samples = [s["db"] for s in samples if "error" not in s["db"]]
    if db_samples:
        total_conns = [d["total"] for d in db_samples]
        active_conns = [d["active"] for d in db_samples]
        idle_conns = [d["idle"] for d in db_samples]
        lines.append(f"  总连接数: avg={round(sum(total_conns)/len(total_conns))}, "
                     f"max={max(total_conns)}")
        lines.append(f"  活跃连接: avg={round(sum(active_conns)/len(active_conns))}, "
                     f"max={max(active_conns)}")
        lines.append(f"  空闲连接: avg={round(sum(idle_conns)/len(idle_conns))}, "
                     f"max={max(idle_conns)}")
    else:
        lines.append("  (无法获取数据)")
    lines.append("")

    # --- Redis ---
    lines.append("--- 4. Redis ---")
    redis_samples = [s["redis"] for s in samples if "error" not in s["redis"]]
    if redis_samples:
        mem_vals = [r["used_memory_mb"] for r in redis_samples]
        client_vals = [r["connected_clients"] for r in redis_samples]
        lines.append(f"  内存使用: avg={round(sum(mem_vals)/len(mem_vals))}MB, "
                     f"max={max(mem_vals)}MB")
        lines.append(f"  峰值内存: {redis_samples[-1].get('used_memory_peak_mb', 'N/A')}MB")
        lines.append(f"  连接客户端: avg={round(sum(client_vals)/len(client_vals))}, "
                     f"max={max(client_vals)}")

        sse_vals = [r["sse_streams"] for r in redis_samples]
        lines.append(f"  SSE streams: avg={round(sum(sse_vals)/len(sse_vals))}, "
                     f"max={max(sse_vals)}")

        # Keyspace growth
        first_keys = redis_samples[0].get("keys", 0)
        last_keys = redis_samples[-1].get("keys", 0)
        lines.append(f"  Keys: {first_keys} → {last_keys} (+{last_keys - first_keys})")
    else:
        lines.append("  (无法获取数据)")
    lines.append("")

    # --- LLM Metrics ---
    lines.append("--- 5. LLM 调用统计 ---")
    llm_samples = [s["llm_metrics"] for s in samples if s["llm_metrics"] and
                   "error" not in s["llm_metrics"]]
    if llm_samples:
        # Aggregate from the last sample (cumulative counters)
        last_llm = llm_samples[-1]
        total_calls = 0
        total_latency = 0
        total_tokens = 0
        task_durations = []

        for key, val in last_llm.items():
            if "metrics:llm:" in key:
                total_calls += val.get("call_count", 0)
                total_latency += val.get("total_latency_ms", 0)
                total_tokens += val.get("total_tokens", 0)
            elif "metrics:task:" in key:
                if "duration_sec" in val:
                    task_durations.append(val["duration_sec"])

        if total_calls > 0:
            lines.append(f"  LLM 调用总次数: {total_calls}")
            lines.append(f"  平均延迟: {round(total_latency / total_calls)}ms")
            lines.append(f"  总延迟: {round(total_latency / 1000, 1)}s")
            if total_tokens > 0:
                lines.append(f"  总 Tokens: {total_tokens}")
        if task_durations:
            lines.append(f"  任务耗时: {[round(d, 1) for d in task_durations]}s")
    else:
        lines.append("  (无 LLM 埋点数据，如需采集请确认 agent 已写入 metrics:*)")
    lines.append("")

    # --- Deployment Plan Validation ---
    lines.append("--- 6. 方案论据验证 ---")
    lines.append("")

    # Validate memory per task
    review_children = []
    for s in samples:
        review_children.extend([p for p in s["workers"].get("review", [])
                                if p["role"] == "child"])
    if review_children:
        peak_mem = max(p["memory_mb"] for p in review_children)
        lines.append(f"  [内存] 单进程峰值: {peak_mem}MB ({round(peak_mem/1024, 1)}GB)")
        lines.append(f"         方案预估: 1.5-2GB/任务 → "
                     f"{'✓ 吻合' if 1500 <= peak_mem <= 2500 else '需要调整'}")

    # Validate DB connections
    if db_samples:
        max_active = max(d["active"] for d in db_samples)
        lines.append(f"  [DB连接] 峰值活跃: {max_active}")
        lines.append(f"           方案预估: 峰值 ~5/任务 → "
                     f"{'✓ 吻合' if max_active < 20 else '⚠ 偏高'}")

    # Validate LLM calls
    if llm_samples and total_calls > 0:
        lines.append(f"  [LLM调用] 总次数: {total_calls}")
        lines.append(f"            方案预估: 50-200 次/任务 → "
                     f"{'✓ 吻合' if 50 <= total_calls <= 300 else '需要调整'}")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def run_monitor(duration: int | None = None):
    """Main monitoring loop."""
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_file = PROJECT_ROOT / f"perf_data_{ts_str}.json"
    report_file = PROJECT_ROOT / f"perf_report_{ts_str}.txt"

    samples = []
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        print(f"\n[收到信号] 正在停止采集...")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"[性能监控] 开始采集 (间隔 {SAMPLE_INTERVAL}s)")
    print(f"[性能监控] 数据文件: {data_file}")
    print(f"[性能监控] 按 Ctrl+C 停止并生成报告")
    if duration:
        print(f"[性能监控] 将在 {duration}s 后自动停止")
    print()

    start_time = time.time()
    idx = 0

    while running:
        if duration and (time.time() - start_time) >= duration:
            print(f"[性能监控] 达到设定时长 {duration}s，停止采集")
            break

        sample = collect_sample(idx)
        samples.append(sample)

        # Print inline status
        sys_info = sample["system"]
        db_info = sample["db"]
        redis_info = sample["redis"]
        review_procs = sample["workers"].get("review", [])
        review_child = [p for p in review_procs if p["role"] == "child"]
        review_mem = sum(p["memory_mb"] for p in review_child) if review_child else 0

        status = (
            f"[{sample['elapsed_sec']:>5d}s] "
            f"CPU={sys_info['cpu_percent']:>5.1f}% "
            f"MEM={sys_info['memory_percent']:>5.1f}% "
            f"Review={len(review_child)}进程/{round(review_mem)}MB "
            f"DB_conn={db_info.get('total', '?')} "
            f"Redis={redis_info.get('used_memory_mb', '?')}MB"
        )
        print(status, flush=True)

        idx += 1
        # Sleep in small increments to respond to signals quickly
        sleep_end = time.time() + SAMPLE_INTERVAL
        while running and time.time() < sleep_end:
            time.sleep(0.5)

    # Save raw data
    with open(data_file, "w") as f:
        json.dump(samples, f, indent=2, ensure_ascii=False)
    print(f"\n[性能监控] 原始数据已保存: {data_file}")

    # Generate report
    report = generate_report(samples)
    with open(report_file, "w") as f:
        f.write(report)
    print(f"[性能监控] 报告已保存: {report_file}")
    print()
    print(report)

    return data_file, report_file


def show_report(data_file: str):
    """Generate report from existing data file."""
    with open(data_file) as f:
        samples = json.load(f)
    report = generate_report(samples)
    print(report)


def main():
    parser = argparse.ArgumentParser(description="Bid Review Task Performance Monitor")
    sub = parser.add_subparsers(dest="command")

    start_parser = sub.add_parser("start", help="Start monitoring")
    start_parser.add_argument("--duration", type=int, default=None,
                              help="Duration in seconds (default: run until Ctrl+C)")

    report_parser = sub.add_parser("report", help="Generate report from data file")
    report_parser.add_argument("data_file", help="Path to perf_data_*.json")

    args = parser.parse_args()

    if args.command == "start":
        run_monitor(duration=args.duration)
    elif args.command == "report":
        show_report(args.data_file)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
