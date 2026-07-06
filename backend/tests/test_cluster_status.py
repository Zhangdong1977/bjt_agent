"""cluster_status 纯函数单元测试（不依赖 celery / Redis / DB）。

worker 名解析、任务归类、节点归并是系统状态页正确性的核心，且全是纯函数，
适合快速回归。celery/Redis 探测在集成层覆盖。
"""

from backend.services.cluster_status import (
    _build_workers,
    _classify_task,
    _group_nodes,
    _parse_worker_name,
)


# ---------------------------------------------------------------------------
# _parse_worker_name
# ---------------------------------------------------------------------------


def test_parse_worker_name_review():
    assert _parse_worker_name("node1_review_0") == ("node1", "review", 0)


def test_parse_worker_name_parser_with_host_suffix():
    # celery 可能给 --hostname 追加 '@host'
    assert _parse_worker_name("node2_parser_3@node2-host") == ("node2", "parser", 3)


def test_parse_worker_name_node_with_underscore():
    # 节点名本身含下划线：非贪婪 + 锚定后缀仍能正确切分
    assert _parse_worker_name("prod_a_review_1@h") == ("prod_a", "review", 1)


def test_parse_worker_name_standalone():
    # 不符合集群命名约定（如本地默认 celery worker）-> standalone
    node, role, idx = _parse_worker_name("celery@localhost")
    assert node == "celery"
    assert role is None
    assert idx is None


# ---------------------------------------------------------------------------
# _classify_task
# ---------------------------------------------------------------------------


def test_classify_review():
    assert _classify_task("backend.tasks.review_tasks.run_review") == "review"


def test_classify_parser():
    assert _classify_task("backend.tasks.document_parser.parse_document") == "parser"


def test_classify_other():
    assert _classify_task("backend.tasks.feedback_tasks.process_feedback") == "other"


# ---------------------------------------------------------------------------
# _build_workers + _group_nodes
# ---------------------------------------------------------------------------


def _sample_probe():
    ping = {
        "node1_review_0@h": {"ok": "pong"},
        "node2_parser_0@h": {"ok": "pong"},
    }
    active = {
        "node1_review_0@h": [
            {"name": "backend.tasks.review_tasks.run_review"},
            {"name": "backend.tasks.experience_tasks.extract_experience"},
        ],
    }
    stats = {
        "node1_review_0@h": {"total": {"backend.tasks.review_tasks.run_review": 5}},
        "node2_parser_0@h": {"total": {"backend.tasks.document_parser.parse_document": 3}},
    }
    return ping, active, stats


def test_build_workers_counts_active_tasks_by_role():
    ping, active, stats = _sample_probe()
    workers, _ = _build_workers(ping, active, stats)
    by_name = {w["name"]: w for w in workers}
    rev = by_name["node1_review_0@h"]
    assert rev["alive"] is True
    assert rev["active_review_tasks"] == 1  # experience 归 other，不计入 review
    assert rev["active_parser_tasks"] == 0
    assert rev["processed"] == 5
    par = by_name["node2_parser_0@h"]
    assert par["alive"] is True
    assert par["active_review_tasks"] == 0
    assert par["active_parser_tasks"] == 0
    assert par["processed"] == 3


def test_group_nodes_aggregates_per_node_and_marks_online():
    ping, active, stats = _sample_probe()
    workers, node_roles = _build_workers(ping, active, stats)
    nodes = {n["name"]: n for n in _group_nodes(workers, node_roles)}
    assert nodes["node1"]["is_online"] is True
    assert nodes["node1"]["roles"] == ["review"]
    assert nodes["node1"]["active_review_tasks"] == 1
    assert nodes["node1"]["alive_workers"] == 1
    assert nodes["node1"]["total_workers"] == 1
    assert nodes["node2"]["roles"] == ["parser"]
    assert nodes["node2"]["is_online"] is True


def test_dead_worker_shows_node_offline():
    # node3 仅出现在 stats（曾注册）但 ping 无应答 -> 离线但仍列出
    ping = {"node1_review_0@h": {"ok": "pong"}}
    active = {}
    stats = {
        "node1_review_0@h": {"total": {}},
        "node3_review_0@h": {"total": {"backend.tasks.review_tasks.run_review": 9}},
    }
    workers, node_roles = _build_workers(ping, active, stats)
    nodes = {n["name"]: n for n in _group_nodes(workers, node_roles)}
    assert nodes["node1"]["is_online"] is True
    assert nodes["node3"]["is_online"] is False
    assert nodes["node3"]["total_workers"] == 1
    assert nodes["node3"]["alive_workers"] == 0
