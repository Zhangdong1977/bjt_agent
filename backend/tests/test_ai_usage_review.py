"""AI 用量看板改动复核脚本（不连数据库、不拉重型依赖）。

复核策略分两层：
A. 纯逻辑模块（usage_context / cost_calculator）：真实 import + 功能测试
   这两个模块只依赖标准库，可独立验证 contextvar 隔离与费用估算。
B. 依赖 backend 包的模块（models/recorder/admin）：py_compile 语法已过，
   此处用 AST 做结构断言（字段/函数/路由存在），避免被 mini_agent/tiktoken/
   chromadb 等重型链式依赖阻断。

运行：D:\miniconda3\envs\bjt-review\python.exe backend\tests\test_ai_usage_review.py
"""
import ast
import os
import sys
import importlib.util
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def _load_module_by_path(modname, filepath):
    """从文件路径加载模块，绕过包 __init__（避免触发重型依赖链）。"""
    spec = importlib.util.spec_from_file_location(modname, filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============ A. 纯逻辑模块功能测试 ============
print("--- A. usage_context / cost_calculator 功能测试 ---")

# usage_context.py 只依赖标准库 contextvars + dataclasses，可直接加载
ctx_mod = _load_module_by_path(
    "review_usage_context",
    os.path.join(_ROOT, "backend", "services", "usage_context.py"),
)
UsageContext = ctx_mod.UsageContext
set_usage_context = ctx_mod.set_usage_context
get_usage_context = ctx_mod.get_usage_context
reset_usage_context = ctx_mod.reset_usage_context

check("默认 context 为 None", get_usage_context() is None)
ctx = UsageContext(
    external_user_id=1001, local_user_id="loc-1", user_name="alice",
    enterprise_name="Acme", interior_user=True, project_id="proj-1",
    task_id="task-1", todo_id="todo-1",
)
tok = set_usage_context(ctx)
got = get_usage_context()
check("set 后可 get", got is not None and got.todo_id == "todo-1" and got.external_user_id == 1001)
reset_usage_context(tok)
check("reset 后回 None", get_usage_context() is None)

# cost_calculator.py 纯函数，可直接加载
cost_mod = _load_module_by_path(
    "review_cost_calculator",
    os.path.join(_ROOT, "backend", "services", "cost_calculator.py"),
)
estimate_cost = cost_mod.estimate_cost

c_ds = estimate_cost(provider="deepseek", model="deepseek-chat",
                     prompt_tokens=1_000_000, completion_tokens=1_000_000, status="success")
# deepseek-chat 未显式列入价目表，走 __default__（= v4-flash 价：miss 1 + output 2）
check("deepseek success 估算 = miss1+output2", c_ds is not None and abs(c_ds - 3.0) < 1e-6, f"got {c_ds}")

# —— deepseek-v4-flash 三档费率（中国官方人民币价目表，每百万 token）——
# 缓存命中 0.02 / 缓存未命中 1.0 / 输出 2.0
c_hit = estimate_cost(provider="deepseek", model="deepseek-v4-flash",
                      prompt_cache_hit_tokens=1_000_000, prompt_cache_miss_tokens=0,
                      completion_tokens=0, status="success")
check("v4-flash 全命中1M = 0.02元", c_hit is not None and abs(c_hit - 0.02) < 1e-9, f"got {c_hit}")
c_miss = estimate_cost(provider="deepseek", model="deepseek-v4-flash",
                      prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1_000_000,
                      completion_tokens=0, status="success")
check("v4-flash 全未命中1M = 1.0元", c_miss is not None and abs(c_miss - 1.0) < 1e-9, f"got {c_miss}")
c_out = estimate_cost(provider="deepseek", model="deepseek-v4-flash",
                     prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0,
                     completion_tokens=1_000_000, status="success")
check("v4-flash 输出1M = 2.0元", c_out is not None and abs(c_out - 2.0) < 1e-9, f"got {c_out}")
# 混合：命中 0.5M + 未命中 0.5M + 输出 0.2M = 0.5*0.02 + 0.5*1 + 0.2*2 = 0.91
c_mix = estimate_cost(provider="deepseek", model="deepseek-v4-flash",
                     prompt_cache_hit_tokens=500_000, prompt_cache_miss_tokens=500_000,
                     completion_tokens=200_000, status="success")
check("v4-flash 混合计价 = 0.91元", c_mix is not None and abs(c_mix - 0.91) < 1e-9, f"got {c_mix}")
# 无 cache 拆分信息时兜底（miss=prompt_tokens）
c_fallback = estimate_cost(provider="deepseek", model="deepseek-v4-flash",
                          prompt_tokens=1_000_000, completion_tokens=0, status="success")
check("v4-flash 无cache拆分兜底 miss=prompt = 1.0元", c_fallback is not None and abs(c_fallback - 1.0) < 1e-9, f"got {c_fallback}")

check("error 不计费", estimate_cost(provider="deepseek", status="error") is None)
check("timeout 不计费", estimate_cost(provider="deepseek", status="timeout") is None)
c_ocr = estimate_cost(provider="baidu_ocr", status="success")
check("baidu_ocr success = 0.015", c_ocr is not None and abs(c_ocr - 0.015) < 1e-9, f"got {c_ocr}")
check("未知 provider 返回 None", estimate_cost(provider="xxx", status="success") is None)
c_default = estimate_cost(provider="deepseek", model="unknown-model",
                          prompt_tokens=1, completion_tokens=0, status="success")
check("deepseek 未知 model 走兜底价", c_default is not None, f"got {c_default}")
# minimax / volcengine 也应可用
check("minimax 可估算", estimate_cost(provider="minimax", model="MiniMax-M2.7-highspeed",
                                       prompt_tokens=0, completion_tokens=0, status="success") is not None)
check("volcengine 可估算", estimate_cost(provider="volcengine", status="success") is not None)

# ============ B. 结构断言（AST，避开重型依赖 import）============
print("\n--- B. models / recorder / admin 结构断言（AST）---")


def _parse(filepath):
    with open(filepath, encoding="utf-8") as f:
        return ast.parse(f.read())


def _class_fields(tree, classname):
    """提取某 class 内的赋值字段名（含 Mapped[...] 与 Column）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == classname:
            names = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    names.append(stmt.target.id)
            return names
    return []


def _func_names(tree):
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) or isinstance(n, ast.AsyncFunctionDef)}


def _router_paths(tree):
    """提取 @router.get/post(...) 装饰的路由 path。"""
    paths = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                if call and isinstance(call.func, ast.Attribute) and call.func.value.id == "router":
                    if call.args and isinstance(call.args[0], ast.Constant):
                        paths.add(call.args[0].value)
    return paths


# B1. ai_usage_record.py 字段
tree_rec = _parse(os.path.join(_ROOT, "backend", "models", "ai_usage_record.py"))
fields = _class_fields(tree_rec, "AiUsageRecord")
expected_fields = {
    "external_user_id", "local_user_id", "user_name", "enterprise_name", "interior_user",
    "project_id", "task_id", "todo_id", "usage_type", "provider", "model", "status",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    "ocr_calls", "ocr_images", "ocr_words_result_num", "image_size_bytes",
    "latency_ms", "endpoint", "error_code", "error_message", "raw_usage",
    "cost_cny", "usage_date",
}
missing = expected_fields - set(fields)
check("AiUsageRecord 字段齐全", not missing, f"缺失: {missing}")
check("session_id 已合并为 task_id", "session_id" not in fields and "task_id" in fields,
      f"fields={fields}")
check("AiUsageRecord 表名 ai_usage_records",
      any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in n.targets)
          and isinstance(n.value, ast.Constant) and n.value.value == "ai_usage_records"
          for n in ast.walk(tree_rec)))

# B2. user.py 新增字段
tree_user = _parse(os.path.join(_ROOT, "backend", "models", "user.py"))
uf = _class_fields(tree_user, "User")
check("User 有 external_user_id", "external_user_id" in uf)
check("User 有 enterprise_name", "enterprise_name" in uf)
check("User 有 interior_user", "interior_user" in uf)

# B3. usage_recorder.py 函数
tree_rr = _parse(os.path.join(_ROOT, "backend", "services", "usage_recorder.py"))
rr_funcs = _func_names(tree_rr)
check("record_llm_usage 存在", "record_llm_usage" in rr_funcs)
check("record_ocr_usage 存在", "record_ocr_usage" in rr_funcs)
check("_write_one 存在", "_write_one" in rr_funcs)
check("_spawn 存在", "_spawn" in rr_funcs)
# 关键：调用 usage_context.get_usage_context（归属来源）+ cost_calculator.estimate_cost
src_rr = open(os.path.join(_ROOT, "backend", "services", "usage_recorder.py"), encoding="utf-8").read()
check("recorder 引用 get_usage_context", "get_usage_context" in src_rr)
check("recorder 引用 estimate_cost", "estimate_cost" in src_rr)
check("recorder fire-and-forget 吞异常", "no running loop" in src_rr and "ignored" in src_rr.lower())

# B4. admin.py 路由
tree_adm = _parse(os.path.join(_ROOT, "backend", "api", "admin.py"))
adm_paths = _router_paths(tree_adm)
check("admin 路由 /usage/records 存在", "/usage/records" in adm_paths, f"paths={adm_paths}")
src_adm = open(os.path.join(_ROOT, "backend", "api", "admin.py"), encoding="utf-8").read()
check("admin 用 X-Internal-Key 鉴权", "X-Internal-Key" in src_adm and "verify_internal_key" in src_adm)
check("admin 游标用 created_at+id 复合", "created_at" in src_adm and "next_cursor" in src_adm)

# B5. auth.py 登录落库扩展字段
src_auth = open(os.path.join(_ROOT, "backend", "api", "auth.py"), encoding="utf-8").read()
check("auth.py 读取 userId", "external_user_id = ext_result.get" in src_auth or '"userId"' in src_auth)
check("auth.py 落库 enterprise_name", "user.enterprise_name = enterprise_name" in src_auth)
check("auth.py JWT claims 含 external_user_id", '"external_user_id"' in src_auth)
check("auth.py mock 分支补默认值", "external_user_name = body.username" in src_auth)

# B6. sub_agent_executor.py 设 usage context
src_sae = open(os.path.join(_ROOT, "backend", "agent", "master", "sub_agent_executor.py"), encoding="utf-8").read()
check("SubAgentExecutor 设 usage_context", "set_usage_context" in src_sae and "UsageContext" in src_sae)
check("SubAgentExecutor finally 重置", "reset_usage_context" in src_sae)
check("SubAgentExecutor 反查 User 拿归属", "select(User)" in src_sae)

# B7. bid_review_agent.py 三出口接入
src_bra = open(os.path.join(_ROOT, "backend", "agent", "bid_review_agent.py"), encoding="utf-8").read()
# success / error / timeout 三处都应调 record_llm_usage
n_llm_calls = src_bra.count("record_llm_usage")
check("LLM 接入至少 3 处出口(success/error/timeout)", n_llm_calls >= 3, f"实际 {n_llm_calls} 处")

# B8. baidu_ocr.py OCR 出口接入
src_ocr = open(os.path.join(_ROOT, "backend", "agent", "tools", "baidu_ocr.py"), encoding="utf-8").read()
n_ocr_calls = src_ocr.count("record_ocr_usage")
check("OCR 接入至少 4 处出口(success/百度错/超时/异常)", n_ocr_calls >= 4, f"实际 {n_ocr_calls} 处")

# B9. config.py 新配置项
src_cfg = open(os.path.join(_ROOT, "backend", "config.py"), encoding="utf-8").read()
check("config 有 usage_sync_api_key", "usage_sync_api_key" in src_cfg)
check("config 有 usage_sync_ip_allowlist", "usage_sync_ip_allowlist" in src_cfg)

# B10. 模型注册到 __init__.py
src_init = open(os.path.join(_ROOT, "backend", "models", "__init__.py"), encoding="utf-8").read()
check("models/__init__ 注册 AiUsageRecord", "from .ai_usage_record import AiUsageRecord" in src_init
      and '"AiUsageRecord"' in src_init)

# B11. api/__init__.py + main.py 注册 admin router
src_api_init = open(os.path.join(_ROOT, "backend", "api", "__init__.py"), encoding="utf-8").read()
src_main = open(os.path.join(_ROOT, "backend", "main.py"), encoding="utf-8").read()
check("api/__init__ 导出 admin_router", "admin_router" in src_api_init)
check("main.py 注册 admin router", "admin_router" in src_main)

# ============ 结果 ============
print("\n" + "=" * 40)
if failures:
    print(f"FAILURES: {len(failures)}\n  - " + "\n  - ".join(failures))
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
