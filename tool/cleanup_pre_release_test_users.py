"""清理（一次性 v3）：预发布库全部测试 fixture 用户及其数据。

- 过滤器覆盖历次 pytest 的全部用户名模式（conftest 与各测试文件自带 fixture）；
  真实账号（手机号/admin 等）不匹配任何模式，不受影响。
- 多趟重试：每条 DELETE 独立事务，FK 冲突的留到下一趟（子先父后最终收敛）；
  projects/users 根节点删除时 CASCADE 带走其余子链。
"""
import asyncio
import os

ROOT = "/home/sales-deploy/apps/bjt-agent/backend"

TEST_FILTER = (
    "(u.username LIKE 'test_user_%' OR u.username LIKE 'interior_user_%'"
    " OR u.username LIKE 'test_%' OR u.username LIKE 'openapi_%'"
    " OR u.username LIKE 'user_b_%' OR u.username LIKE 'concurrent_user_%'"
    " OR u.username LIKE 'shared_ip_user_%' OR u.username LIKE 'ratelimit_user_%'"
    " OR u.username LIKE 'bill_owner_%' OR u.username LIKE 'bill_interior_%'"
    " OR u.username LIKE 'sys_test_%' OR u.username LIKE 'window_test_%'"
    " OR u.username LIKE 'e2e_%')"
)

USERS_SUB = "(SELECT id FROM users u WHERE " + TEST_FILTER + ")"
PROJECTS_SUB = "(SELECT p.id FROM projects p JOIN users u ON u.id = p.user_id WHERE " + TEST_FILTER + ")"


def load_env(path):
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


env = load_env(os.path.join(ROOT, ".env"))
url = env.get("DATABASE_URL") or env.get("SQLALCHEMY_DATABASE_URL")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

STATEMENTS = [
    ("task_dispatch_outbox",
     "DELETE FROM task_dispatch_outbox o WHERE o.task_kind IN ('bid_wizard_write','bid_wizard_qa','bid_wizard_index')"
     " AND o.task_id IN (SELECT t.id FROM bid_writing_tasks t JOIN bid_wizards w ON w.id=t.wizard_id"
     " JOIN users u ON u.id=w.user_id WHERE " + TEST_FILTER +
     " UNION SELECT q.id FROM bid_wizard_qa_tasks q JOIN bid_wizards w ON w.id=q.wizard_id"
     " JOIN users u ON u.id=w.user_id WHERE " + TEST_FILTER +
     " UNION SELECT i.id FROM bid_wizard_index_tasks i JOIN bid_wizards w ON w.id=i.wizard_id"
     " JOIN users u ON u.id=w.user_id WHERE " + TEST_FILTER + ")"),
    ("ai_usage_records", "DELETE FROM ai_usage_records r WHERE r.local_user_id IN " + USERS_SUB),
    ("ai_usage_task_summary",
     "DELETE FROM ai_usage_task_summary s WHERE s.local_user_id IN " + USERS_SUB +
     " OR s.id IN (SELECT t.id FROM bid_writing_tasks t JOIN bid_wizards w ON w.id=t.wizard_id"
     " JOIN users u ON u.id=w.user_id WHERE " + TEST_FILTER + ")"),
    ("experience_cluster_memberships",
     "DELETE FROM experience_cluster_memberships m WHERE m.case_id IN"
     " (SELECT c.id FROM experience_cases c WHERE c.project_id IN " + PROJECTS_SUB + ")"),
    ("experience_feedback",
     "DELETE FROM experience_feedback f WHERE f.user_id IN " + USERS_SUB + " OR f.project_id IN " + PROJECTS_SUB),
    ("experience_cases", "DELETE FROM experience_cases c WHERE c.project_id IN " + PROJECTS_SUB),
    ("project_review_results", "DELETE FROM project_review_results r WHERE r.project_id IN " + PROJECTS_SUB),
    ("blind_check_tasks", "DELETE FROM blind_check_tasks b WHERE b.user_id IN " + USERS_SUB),
]


async def main():
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            n_users = (await conn.execute(text("SELECT count(*) FROM users u WHERE " + TEST_FILTER))).scalar()
            print("test users to delete: %d" % n_users)
            if not n_users:
                print("nothing to do")
                return
            fk_rows = await conn.execute(text(
                "SELECT kcu.table_name, kcu.column_name FROM information_schema.key_column_usage kcu "
                "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = kcu.constraint_name "
                "JOIN information_schema.table_constraints tc ON tc.constraint_name = kcu.constraint_name"
                " AND tc.constraint_type = 'FOREIGN KEY' "
                "WHERE kcu.table_name <> 'users' AND ccu.table_name = 'users'"))
            for table, column in fk_rows:
                if table in ("projects", "experience_feedback"):
                    continue
                STATEMENTS.append((table, "DELETE FROM %s x WHERE x.%s IN %s" % (table, column, USERS_SUB)))
        STATEMENTS.append(("projects", "DELETE FROM projects p WHERE " + TEST_FILTER.replace("u.", "p.")))
        STATEMENTS.append(("users", "DELETE FROM users u WHERE " + TEST_FILTER))

        remaining = list(STATEMENTS)
        for pass_no in range(8):
            nxt = []
            progress = False
            for label, sql in remaining:
                try:
                    async with engine.begin() as conn:
                        res = await conn.execute(text(sql))
                    if res.rowcount:
                        print("  p%d %-30s -%d" % (pass_no, label, res.rowcount))
                    progress = True
                except Exception as exc:
                    if "ForeignKeyViolation" in str(exc):
                        nxt.append((label, sql))
                    else:
                        print("  p%d %-30s HARD-ERR %s" % (pass_no, label, str(exc)[:130]))
                        nxt.append((label, sql))
            remaining = nxt
            if not remaining:
                break
            if not progress:
                print("STUCK: %s" % [l for l, _ in remaining])
                break
        async with engine.connect() as conn:
            left = (await conn.execute(text("SELECT count(*) FROM users u WHERE " + TEST_FILTER))).scalar()
            total = (await conn.execute(text("SELECT count(*) FROM users"))).scalar()
            print("remaining test users: %d; total users now: %d" % (left, total))
    finally:
        await engine.dispose()


asyncio.run(main())
