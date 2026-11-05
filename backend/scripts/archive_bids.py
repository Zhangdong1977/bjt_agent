#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户标书归档下线工具（数据最小化：生产不留标书明文）。

用法：
  python backend/scripts/archive_bids.py                        # 交互式向导
  python backend/scripts/archive_bids.py plan [--target prod]   # 只读盘点
  python backend/scripts/archive_bids.py verify --batch-dir DIR [--extract-to DIR]
      # 重验本地批次；--extract-to=验证通过后顺带把解密文件落到该目录/<unit_id>/（明文，看完即删）
  python backend/scripts/archive_bids.py restore                # 从本地归档回放
  python backend/scripts/archive_bids.py cleanup-staging [--older-than-days 14]

新机器直接跑向导：连接探测失败会交互式收集 主机/用户/跳板（记忆到
~/.bjt_archive.cfg，密码每次运行输入一次、绝不保存）。登录默认用户名+密码
（需 pip install paramiko）；也可 --identity 指定私钥改密钥登录。

runbook：sales/doc/workspace/24-bid-archive-offload.md。
"""

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

try:  # Windows 控制台中文输出兜底
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VERSION = "1.4.6"
PASS_ENV = "BJT_ARCHIVE_PASS"
INNER_DIR = "__archive__"  # 加密包内的元数据目录（manifest / db 快照）
DEFAULT_RETENTION_DAYS = 7
DEFAULT_STAGING_KEEP_DAYS = 14

# ── 业务状态常量（与 backend/services/task_lifecycle.py 口径一致）─────────
TASK_TERMINAL = ("completed", "failed", "cancelled")
BILLING_UNSETTLED = ("pending", "processing", "retry")
DOC_ACTIVE = ("pending", "parsing")  # documents.status 活跃态

# documents 表的全部文件路径列（孤儿文档单元按这些列逐文件收集）
DOC_PATH_COLUMNS = (
    "file_path",
    "parsed_html_path",
    "parsed_markdown_path",
    "docling_json_path",
    "artifact_manifest_path",
    "evidence_blocks_path",
    "source_snapshot_path",
    "parsed_images_dir",  # 目录
)

# workspace 顶级服务目录（清扫/围栏判断用）
SERVICE_TOPDIRS = {"bid-wizard", "bid-draft", "blind-check", "knowledge",
                   ".duplicate_cache", ".ocr_image_cache"}
CACHES = (".duplicate_cache", ".ocr_image_cache")  # 可再生缓存：到期直接删、不备份

# 运行目标（本地侧 paramiko 连接用）：用户名+密码登录；跳板/私钥可选。
# pre-release 字段在彩排时按 07 号文档核对/覆盖。
TARGETS = {
    "prod": {
        "host": "192.168.40.110", "user": "root",
        "jump": "10.1.3.241", "jump_user": "root",
        "identity": None,
        "repo": "/home/bjt/bjt_agent",
        "preamble": "source /home/bjt/miniconda3/etc/profile.d/conda.sh && conda activate py311",
        "staging": "/srv/bjt_archive/staging",
        "desc": "生产（node1，workspace 实体盘在本机；经 prod 内网跳板）",
    },
    "pre-release": {
        "host": "10.0.3.23", "user": "root",
        "jump": None, "jump_user": None, "identity": None,
        "repo": "/home/sales-deploy/apps/bjt-agent",
        "preamble": "",
        "staging": "/home/sales-deploy/bjt_archive/staging",
        "desc": "预发布（彩排用）",
    },
}

# 节点模式实际使用的路径（节点上运行，与本地 target 概念无关）
NODE_REPO_DEFAULT = "/home/bjt/bjt_agent"
NODE_STAGING_DEFAULT = "/srv/bjt_archive/staging"

LOCAL_CFG = Path.home() / ".bjt_archive.cfg"


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def fmt_bytes(n):
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(x) < 1024 or unit == "TB":
            return f"{n}B" if unit == "B" else f"{x:.1f}{unit}"
        x /= 1024.0


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_stream(src):
    h = hashlib.sha256()
    while True:
        b = src.read(1024 * 1024)
        if not b:
            break
        h.update(b)
    return h.hexdigest()


def find_openssl():
    exe = shutil.which("openssl")
    if exe:
        return exe
    for cand in (r"C:\Program Files\Git\usr\bin\openssl.exe", "/usr/bin/openssl"):
        if Path(cand).exists():
            return cand
    sys.exit("找不到 openssl（本地验证请在 git-bash 环境运行）")


def log_evt(sink, event, **kw):
    """审计日志：JSONL，节点侧落暂存区，本地侧落归档根 logs/。"""
    line = json.dumps({"ts": now_iso(), "event": event, **kw}, ensure_ascii=False)
    print(f"[{event}] " + ", ".join(f"{k}={v}" for k, v in kw.items()),
          file=sys.stderr)
    if sink:
        sink.write(line + "\n")
        sink.flush()


def openssl_args(op, exe="openssl"):
    """构造统一的 openssl enc 参数（op='-e'/'-d'，两侧参数必须完全一致）。"""
    return [exe, "enc", op, "-aes-256-cbc", "-pbkdf2", "-iter", "600000",
            "-md", "sha256", f"-pass", f"env:{PASS_ENV}"]


# ══════════════════════════════════════════════════════════════════════════
# 远程执行（paramiko，用户名+密码登录；远端命令经 stdin 注入 bash -s）
# 密码仅驻留内存：每台主机一次运行只问一次，绝不写入任何文件/进程参数。
# ══════════════════════════════════════════════════════════════════════════

_REMOTE = None


def _import_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        sys.exit("缺 paramiko（用户名/密码登录需要）：pip install paramiko")


def _parse_userhost(s):
    """user@host → (user, host)；无 @ 则 (None, s)。"""
    if "@" in s:
        u, _, h = s.partition("@")
        return u, h
    return None, s


def apply_ssh_overrides(args):
    """--ssh/--jump/--identity 覆盖目标连接信息（USER@HOST 形式）。"""
    t = TARGETS[args.target]
    if getattr(args, "ssh_host", None):
        u, h = _parse_userhost(args.ssh_host)
        t["host"], t["user"] = h, u or t["user"]
    if getattr(args, "ssh_jump", None):
        ju, jh = _parse_userhost(args.ssh_jump)
        t["jump"], t["jump_user"] = jh, ju or t["user"]
    if getattr(args, "ssh_identity", None):
        t["identity"] = str(Path(args.ssh_identity).expanduser())
    reset_remote()


def apply_saved_ssh(cfg, target):
    """沿用上次向导交互收集并记忆的连接配置（CLI 参数仍优先于记忆值）。"""
    saved = cfg.get(f"ssh_{target}") or {}
    t = TARGETS[target]
    for k in ("host", "user", "jump", "jump_user", "identity"):
        if saved.get(k):
            t[k] = str(Path(saved[k]).expanduser()) if k == "identity" else saved[k]


def _drain_stderr(f, sink, echo=True):
    """后台线程流式读 stderr：实时 [节点] 前缀回显 + 累积到 sink 供报错用。

    paramiko 的 channel file 按版本/模式 readline 可能返回 str 或 bytes，
    统一归一成 bytes 再入 sink（下游 b"".join / .decode 才稳）。
    """
    try:
        while True:
            line = f.readline()
            if not line:  # str '' 与 bytes b'' 的 EOF 都命中
                break
            if isinstance(line, str):
                line = line.encode("utf-8", "replace")
            sink.append(line)
            if echo:
                sys.stderr.write("[节点] " + line.decode("utf-8", "replace"))
                sys.stderr.flush()
    except Exception:
        pass


class Remote:
    """paramiko 连接（可选经跳板 direct-tcpip 通道）；连接缓存到进程结束。"""

    def __init__(self, t):
        self.t = t
        self._jump = None
        self._client = None

    def _connect_client(self, host, user, label, sock=None):
        paramiko = _import_paramiko()
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        identity = self.t.get("identity")
        env_pass = os.environ.get("BJT_ARCHIVE_JUMP_PASS" if label == "跳板"
                                  else "BJT_ARCHIVE_SSH_PASS")
        last = None
        for _ in range(3):  # 密码输错可重输（最多 3 次）
            kwargs = dict(hostname=host, username=user, port=22,
                          look_for_keys=False, allow_agent=False,
                          sock=sock, timeout=15, banner_timeout=15,
                          auth_timeout=15)
            if identity:
                kwargs["key_filename"] = identity
            else:
                kwargs["password"] = env_pass or getpass.getpass(
                    f"{label} {user}@{host} 密码: ")
            try:
                cli.connect(**kwargs)
                return cli
            except paramiko.ssh_exception.AuthenticationException as e:
                last = e
                if env_pass:  # 环境变量提供的密码失败不重试
                    break
        raise RuntimeError(f"{label} {user}@{host} 认证失败: {last}")

    def client(self):
        if self._client is None:
            sock = None
            if self.t.get("jump"):
                self._jump = self._connect_client(
                    self.t["jump"], self.t.get("jump_user") or "root", "跳板")
                sock = self._jump.get_transport().open_channel(
                    "direct-tcpip", (self.t["host"], 22), ("127.0.0.1", 0))
            self._client = self._connect_client(self.t["host"], self.t["user"],
                                                "目标", sock=sock)
        return self._client

    def run(self, script, args=(), binary_out=None, live_stderr=False):
        """执行 bash -s（脚本经 stdin 注入）；返回 SimpleNamespace(stdout=bytes)。

        binary_out: 给定 Path 时 stdout 流式直写该文件（tar 拉回用）。
        live_stderr: 远端 stderr 逐行实时回显到本地控制台（[节点] 前缀），
                     兼作错误信息收集——长任务（archive/purge）不再"黑盒卡住"。
        """
        cmd = "bash -s --" + "".join(" " + shlex.quote(a) for a in args)
        stdin, stdout, stderr = self.client().exec_command(cmd)
        stdin.write(script.encode("utf-8"))
        stdin.channel.shutdown_write()
        sink = []
        reader = None
        if live_stderr:
            reader = threading.Thread(target=_drain_stderr,
                                      args=(stderr, sink), daemon=True)
            reader.start()
        chan = stdout.channel
        if binary_out:
            with open(binary_out, "wb") as f:
                while True:
                    b = chan.recv(65536)
                    if not b:
                        break
                    f.write(b)
            out = b""
        else:
            out = stdout.read()
        if reader is not None:
            reader.join(timeout=30)
        err = b"".join(sink) if live_stderr else stderr.read()
        if isinstance(err, str):  # paramiko 文本模式同源坑
            err = err.encode("utf-8", "replace")
        rc = chan.recv_exit_status()
        if rc != 0:
            raise RuntimeError(f"远端执行失败 (rc={rc}):\n"
                               f"{err.decode('utf-8', 'replace').strip()}")
        return SimpleNamespace(stdout=out)

    def upload(self, local, remote_dir, remote_name=None):
        """SFTP 上传单个文件（等价 scp，密码复用连接）。

        remote_name: 指定远端文件名（默认取本地名）。本地名带"(2)"等
        下载副本后缀时必须指定，否则远端路径与预期不一致。
        """
        self.run("mkdir -p " + shlex.quote(remote_dir) + "\n")
        name = remote_name or Path(local).name
        sftp = self.client().open_sftp()
        try:
            sftp.put(str(local), remote_dir.rstrip("/") + "/" + name)
        finally:
            sftp.close()

    def run_extract_tar(self, script, args, dest_dir):
        """执行远端 tar 流命令，stdout 不落中间文件、直接流式解包到 dest_dir。

        逐成员做路径围栏校验（防远端 tar 成员路径逃逸）；瞬时磁盘占用=批次本体。
        """
        cmd = "bash -s --" + "".join(" " + shlex.quote(a) for a in args)
        stdin, stdout, stderr = self.client().exec_command(cmd)
        stdin.write(script.encode("utf-8"))
        stdin.channel.shutdown_write()
        dest = dest_dir.resolve()
        count = 0
        with tarfile.open(fileobj=stdout, mode="r|") as tf:
            for m in tf:
                if not (m.isreg() or m.isdir()):
                    continue
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise RuntimeError(f"可疑 tar 成员: {m.name}")
                target = (dest / m.name).resolve()
                if target != dest and dest not in target.parents:
                    raise RuntimeError(f"可疑 tar 成员: {m.name}")
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                with open(target, "wb") as f:
                    shutil.copyfileobj(src, f, 1024 * 1024)
                count += 1
        err = stderr.read()
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            raise RuntimeError(f"远端执行失败 (rc={rc}):\n"
                               f"{err.decode('utf-8', 'replace').strip()}")
        return count

    def run_stream_out(self, script, out_file):
        """执行 bash -s（脚本经 stdin 注入），stdout 流式写入已定位的 out_file。

        分块拉回专用：不缓冲整个输出、不做本地中间文件；返回写入字节数。
        通道中断（OSError/EOF）直接抛出，由上层分块重试兜底。
        """
        stdin, stdout, stderr = self.client().exec_command("bash -s --")
        stdin.write(script.encode("utf-8"))
        stdin.channel.shutdown_write()
        total = 0
        while True:
            b = stdout.channel.recv(65536)
            if not b:
                break
            out_file.write(b)
            total += len(b)
        rc = stdout.channel.recv_exit_status()
        err = stderr.read()
        if isinstance(err, str):  # paramiko 文本模式同源坑
            err = err.encode("utf-8", "replace")
        if rc != 0:
            raise RuntimeError(f"远端执行失败 (rc={rc}): "
                               f"{err.decode('utf-8', 'replace').strip()}")
        return total

    def close(self):
        for c in (self._client, self._jump):
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
        self._client = self._jump = None


def get_remote(target):
    global _REMOTE
    if _REMOTE is None:
        _REMOTE = Remote(TARGETS[target])
    return _REMOTE


def reset_remote():
    global _REMOTE
    if _REMOTE is not None:
        _REMOTE.close()
    _REMOTE = None


def ssh_call(target, script_lines, remote_args=(), pass_env=None, binary_out=None,
             live_stderr=False):
    """在目标节点执行一段 bash 脚本（经 stdin 注入）。

    pass_env: 归档口令等敏感值经环境变量注入远端（不进 ps、不落盘）。
    """
    t = TARGETS[target]
    lines = ["set -euo pipefail", t["preamble"]]
    for k, v in (pass_env or {}).items():
        lines.append(f"export {k}={shlex.quote(v)}")
    lines += list(script_lines)
    return get_remote(target).run("\n".join(lines) + "\n", remote_args, binary_out,
                                  live_stderr=live_stderr)


_NODE_SCRIPT_UPLOADED = False


def node_py(target, node_args, pass_env=None):
    """调起节点模式（自举）：先上传本地脚本副本到节点暂存区，再从那里执行。

    免预先部署——节点跑的永远是操作员本地这份脚本，也不会版本漂移。
    子命令直接拼进脚本命令行（不依赖 bash 位置参数转发）。
    """
    global _NODE_SCRIPT_UPLOADED
    remote_script = f"{TARGETS[target]['staging']}/archive_bids.py"
    if not _NODE_SCRIPT_UPLOADED:
        # 远端固定用规范名 archive_bids.py（不随本地文件名漂移，如 "(2)" 副本）
        get_remote(target).upload(Path(__file__).resolve(),
                                  TARGETS[target]["staging"],
                                  remote_name="archive_bids.py")
        _NODE_SCRIPT_UPLOADED = True
        print(f"已上传节点侧执行副本 v{VERSION} → {remote_script}")
    cmd = (f"exec python {shlex.quote(remote_script)} __node"
           + "".join(" " + shlex.quote(a) for a in node_args))
    return ssh_call(target, [cmd], remote_args=(), pass_env=pass_env,
                    live_stderr=True)  # 远端进度实时回显，长任务不再黑盒


def node_json(target, node_args, pass_env=None):
    return json.loads(node_py(target, node_args, pass_env=pass_env)
                      .stdout.decode("utf-8"))


def scp_to(target, local, remote_dir):
    get_remote(target).upload(local, remote_dir)


# ══════════════════════════════════════════════════════════════════════════
# 节点模式：上下文 / 选择 / 收集 / 打包 / 删除 / 回抽
# ══════════════════════════════════════════════════════════════════════════

def _import_asyncpg():
    try:
        import asyncpg
        return asyncpg
    except ImportError:
        sys.exit("节点模式需要 asyncpg（用 backend conda py311 环境运行）")


class NodeCtx:
    """节点侧上下文：.env / workspace 真实根 / staging / DB 连接。"""

    def __init__(self):
        self.repo = Path(os.environ.get("BJT_ARCHIVE_REPO", NODE_REPO_DEFAULT))
        self.env = self._load_env(self.repo / "backend" / ".env")
        ws = Path(self.env.get("WORKSPACE_DIR", "./workspace"))
        if not ws.is_absolute():
            ws = self.repo / ws
        if not ws.exists():
            sys.exit(f"workspace 根不存在: {ws}")
        self.ws_root = ws.resolve()  # 经 symlink 解析到 /srv/bjt_workspace
        self.staging = Path(os.environ.get("BJT_ARCHIVE_STAGING",
                                           NODE_STAGING_DEFAULT)).resolve()
        self.staging.mkdir(parents=True, exist_ok=True)
        os.chmod(self.staging, 0o700)
        self.conn = None

    @staticmethod
    def _load_env(path):
        env = {}
        if not path.exists():
            return env
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
        return env

    async def db(self):
        if self.conn is None:
            asyncpg = _import_asyncpg()
            dsn = os.environ.get("DATABASE_URL") or self.env.get("DATABASE_URL", "")
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
            if not dsn:
                sys.exit("backend/.env 缺 DATABASE_URL")
            self.conn = await asyncpg.connect(dsn=dsn, timeout=15)
        return self.conn

    # ── 路径围栏 ───────────────────────────────────────────────────────────
    def fence(self, p: Path, what="路径"):
        rp = p.resolve()
        if self.ws_root != rp and self.ws_root not in rp.parents:
            sys.exit(f"围栏拦截：{what} {p} 不在 workspace 根 {self.ws_root} 内")
        return rp

    def rel_of(self, abs_path: Path) -> str:
        return abs_path.resolve().relative_to(self.ws_root).as_posix()

    def under_knowledge(self, abs_path: Path) -> bool:
        rp = abs_path.resolve()
        kp = (self.ws_root / "knowledge").resolve()
        return kp == rp or kp in rp.parents


# ── 选择逻辑：哪些单元可以归档 ────────────────────────────────────────────

async def select_project_units(ctx, days):
    """项目单元：安静 N 天 + 各任务表全终态 + 计费全结算 + 无解析中文档。"""
    db = await ctx.db()
    rows = await db.fetch(
        """
        WITH cand AS (
          SELECT p.id, p.user_id,
                 GREATEST(
                   p.updated_at,
                   COALESCE((SELECT MAX(d.updated_at) FROM documents d
                             WHERE d.project_id = p.id), p.updated_at),
                   COALESCE((SELECT MAX(t.updated_at) FROM review_tasks t
                             WHERE t.project_id = p.id), p.updated_at),
                   COALESCE((SELECT MAX(w.updated_at) FROM bid_writing_tasks w
                             WHERE w.project_id = p.id), p.updated_at),
                   COALESCE((SELECT MAX(b.updated_at) FROM bid_draft_tasks b
                             WHERE b.project_id = p.id), p.updated_at),
                   COALESCE((SELECT MAX(z.updated_at) FROM bid_wizards z
                             WHERE z.project_id = p.id), p.updated_at)
                 ) AS last_activity
          FROM projects p
        )
        SELECT c.id, c.user_id, c.last_activity FROM cand c
        WHERE c.last_activity < now() - make_interval(days => $1)
          AND NOT EXISTS (SELECT 1 FROM review_tasks t WHERE t.project_id = c.id
                          AND t.status != ALL($2::text[]))
          AND NOT EXISTS (SELECT 1 FROM review_tasks t WHERE t.project_id = c.id
                          AND t.billing_status = ANY($3::text[]))
          AND NOT EXISTS (SELECT 1 FROM bid_writing_tasks w WHERE w.project_id = c.id
                          AND w.status != ALL($2::text[]))
          AND NOT EXISTS (SELECT 1 FROM bid_writing_tasks w WHERE w.project_id = c.id
                          AND w.billing_status = ANY($3::text[]))
          AND NOT EXISTS (SELECT 1 FROM bid_draft_tasks b WHERE b.project_id = c.id
                          AND b.status != ALL($2::text[]))
          AND NOT EXISTS (SELECT 1 FROM bid_draft_tasks b WHERE b.project_id = c.id
                          AND b.billing_status = ANY($3::text[]))
          AND NOT EXISTS (SELECT 1 FROM bid_wizards z WHERE z.project_id = c.id
                          AND z.status = 'active')
          AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.project_id = c.id
                          AND d.status = ANY($4::text[]))
        ORDER BY c.last_activity ASC
        """,
        days, list(TASK_TERMINAL), list(BILLING_UNSETTLED), list(DOC_ACTIVE),
    )
    return [{"type": "project", "key": r["id"], "user_id": r["user_id"],
             "last_activity": r["last_activity"].isoformat()} for r in rows]


async def select_orphan_doc_units(ctx, days):
    """孤儿文档：project_id 为空（_drafts/_api），安静 N 天且不在解析中。"""
    db = await ctx.db()
    rows = await db.fetch(
        """SELECT id, owner_user_id, updated_at FROM documents
           WHERE project_id IS NULL
             AND status != ANY($2::text[])
             AND updated_at < now() - make_interval(days => $1)
           ORDER BY updated_at ASC""",
        days, list(DOC_ACTIVE))
    return [{"type": "orphan_doc", "key": r["id"], "user_id": r["owner_user_id"],
             "last_activity": r["updated_at"].isoformat()} for r in rows]


async def select_blind_units(ctx, days):
    """暗标任务目录（mini-agent 草稿区，用户 Word 派生物）：终态+结算+安静 N 天。"""
    db = await ctx.db()
    rows = await db.fetch(
        """SELECT id, user_id, updated_at FROM blind_check_tasks
           WHERE status = ANY($2::text[])
             AND billing_status != ANY($3::text[])
             AND updated_at < now() - make_interval(days => $1)
           ORDER BY updated_at ASC""",
        days, list(TASK_TERMINAL), list(BILLING_UNSETTLED))
    return [{"type": "blind_check", "key": r["id"], "user_id": r["user_id"],
             "last_activity": r["updated_at"].isoformat()} for r in rows]


async def sweep_collect(ctx, user_id, cutoff_ts):
    """清扫文件枚举（选择/打包/删除共用同一规则）。

    范围：该用户目录下 _drafts/_api/无 DB 项目行的目录 中 mtime<cutoff 的文件；
    保护：DB documents 行引用中的文件（活跃草稿）、在册项目目录、knowledge。
    """
    db = await ctx.db()
    user_dir = ctx.ws_root / user_id
    if not user_dir.is_dir():
        return []
    known_projects = {r["id"] for r in await db.fetch(
        "SELECT id FROM projects WHERE user_id = $1", user_id)}
    protected = set()
    for r in await db.fetch(
            "SELECT " + ", ".join(DOC_PATH_COLUMNS) +
            " FROM documents WHERE owner_user_id = $1", user_id):
        for c in DOC_PATH_COLUMNS:
            if r[c]:
                try:
                    protected.add(str(Path(r[c]).resolve()))
                except OSError:
                    pass
    out = []
    for entry in sorted(user_dir.iterdir()):
        if not entry.is_dir() or entry.name in SERVICE_TOPDIRS:
            continue
        if entry.name not in ("_drafts", "_api") and entry.name in known_projects:
            continue  # 在册项目目录交给项目单元
        for root, _dirs, fnames in os.walk(entry):
            for fn in fnames:
                p = Path(root) / fn
                try:
                    if p.stat().st_mtime >= cutoff_ts:
                        continue
                    rp = p.resolve()
                    if str(rp) in protected:
                        continue
                    out.append(rp)
                except OSError:
                    continue
    return out


async def select_sweep_units(ctx, days):
    units = []
    cutoff = time.time() - days * 86400
    for user_dir in sorted(ctx.ws_root.iterdir()):
        if not user_dir.is_dir() or user_dir.name.startswith(".") \
                or user_dir.name in SERVICE_TOPDIRS:
            continue
        if await sweep_collect(ctx, user_dir.name, cutoff):
            units.append({"type": "sweep", "key": user_dir.name,
                          "user_id": user_dir.name, "last_activity": None})
    return units


async def select_units(ctx, days):
    return (await select_project_units(ctx, days)
            + await select_orphan_doc_units(ctx, days)
            + await select_blind_units(ctx, days)
            + await select_sweep_units(ctx, days))


async def blocker_stats(ctx):
    """plan 摘要用的阻塞统计（各原因影响的独立项目数）。"""
    db = await ctx.db()
    rows = await db.fetch(
        """
        SELECT
          (SELECT COUNT(DISTINCT project_id) FROM review_tasks
            WHERE status != ALL($1::text[]))                          AS review_active,
          (SELECT COUNT(DISTINCT project_id) FROM review_tasks
            WHERE billing_status = ANY($2::text[]))                   AS review_unsettled,
          (SELECT COUNT(DISTINCT project_id) FROM bid_writing_tasks
            WHERE status != ALL($1::text[]))                          AS writing_active,
          (SELECT COUNT(DISTINCT project_id) FROM bid_draft_tasks
            WHERE status != ALL($1::text[]))                          AS draft_active,
          (SELECT COUNT(DISTINCT project_id) FROM bid_wizards
            WHERE status = 'active')                                  AS wizard_active,
          (SELECT COUNT(DISTINCT project_id) FROM documents
            WHERE status = ANY($3::text[]))                           AS doc_parsing
        """,
        list(TASK_TERMINAL), list(BILLING_UNSETTLED), list(DOC_ACTIVE))
    r = dict(rows[0])
    return {k: int(v) for k, v in r.items()}


# ── 单元文件收集 ──────────────────────────────────────────────────────────

async def collect_unit_files(ctx, unit, with_hash=True, archive_start_ts=None,
                             retention_days=DEFAULT_RETENTION_DAYS):
    """返回 [(绝对路径, 相对路径, sha256|None, size)]；文件缺失/越界则跳过。

    archive_start_ts: 删除阶段传入（= 打包开始时刻），mtime 晚于它的文件
    跳过不删（归档后新落盘的文件留给下一批）。
    retention_days: 清扫单元在选择/打包阶段的文件年龄口径（与选择一致）。
    """

    def take(p, out):
        try:
            if p.is_symlink():
                return  # 拒绝符号链接，避免打包/校验歧义
            rp = p.resolve()
            if not rp.is_file() or ctx.under_knowledge(rp):
                return
            ctx.fence(rp)
            st = rp.stat()
        except OSError:
            return
        if archive_start_ts is not None and st.st_mtime >= archive_start_ts:
            return
        out.append((rp, ctx.rel_of(rp),
                    sha256_file(rp) if with_hash else None, st.st_size))

    def walk_dir(d, out):
        if not d.is_dir():
            return
        for root, dirs, fnames in os.walk(d):
            dirs[:] = [x for x in dirs if not x.startswith(".nfs")]
            for fn in fnames:
                take(Path(root) / fn, out)

    files = []
    if unit["type"] == "project":
        db = await ctx.db()
        user_id, pid = unit["user_id"], unit["key"]
        walk_dir(ctx.ws_root / user_id / pid, files)
        # 关联外置目录：AI编标 wizard 目录 + 标书生成 V1 任务目录
        for r in await db.fetch(
                "SELECT id FROM bid_wizards WHERE project_id = $1", pid):
            walk_dir(ctx.ws_root / "bid-wizard" / r["id"], files)
        for r in await db.fetch(
                "SELECT id FROM bid_draft_tasks WHERE project_id = $1", pid):
            walk_dir(ctx.ws_root / "bid-draft" / r["id"], files)
    elif unit["type"] == "orphan_doc":
        db = await ctx.db()
        row = await db.fetchrow(
            "SELECT project_id, " + ", ".join(DOC_PATH_COLUMNS) +
            " FROM documents WHERE id = $1", unit["key"])
        if row is None or row["project_id"]:
            return []  # 行已消失或已关联项目（交给项目单元）
        for col in DOC_PATH_COLUMNS:
            v = row[col]
            if not v:
                continue
            if col == "parsed_images_dir":
                walk_dir(Path(v), files)
            else:
                take(Path(v), files)
    elif unit["type"] == "blind_check":
        walk_dir(ctx.ws_root / "blind-check" / unit["key"], files)
    elif unit["type"] == "sweep":
        # 选择/打包阶段按保留期口径；删除阶段一律以打包开始时刻为界
        cutoff = archive_start_ts if archive_start_ts is not None \
            else time.time() - retention_days * 86400
        for rp in await sweep_collect(ctx, unit["key"], cutoff):
            take(rp, files)
    # 去重（同一文件可能被多个来源命中）
    seen, uniq = set(), []
    for rp, rel, h, sz in files:
        if rel not in seen:
            seen.add(rel)
            uniq.append((rp, rel, h, sz))
    return uniq


# ── DB 快照（只读，进加密包，供恢复/审计时人工比对）───────────────────────

async def db_snapshot(ctx, unit):
    db = await ctx.db()

    async def rows(table, where, arg):
        return [json.dumps(dict(r), default=str, ensure_ascii=False)
                for r in await db.fetch(
                    f"SELECT row_to_json(t) FROM {table} t WHERE {where}", arg)]

    snap = {"unit": {k: unit[k] for k in ("type", "key", "user_id", "last_activity")
                     if unit.get(k) is not None}}
    if unit["type"] == "project":
        for t in ("projects", "documents", "review_tasks", "bid_wizards",
                  "bid_writing_tasks", "bid_draft_tasks"):
            snap[t] = await rows(t, "project_id = $1" if t != "projects" else "id = $1",
                                 unit["key"])
    elif unit["type"] == "orphan_doc":
        snap["documents"] = await rows("documents", "id = $1", unit["key"])
    elif unit["type"] == "blind_check":
        snap["blind_check_tasks"] = await rows("blind_check_tasks", "id = $1",
                                               unit["key"])
    return snap


# ── 节点子命令 ────────────────────────────────────────────────────────────

def cmd_node_env_info(args):
    ctx = NodeCtx()
    free = shutil.disk_usage(ctx.staging).free
    info = {"hostname": socket.gethostname(), "repo": str(ctx.repo),
            "workspace_root": str(ctx.ws_root),
            "staging": str(ctx.staging), "staging_free": free,
            "openssl": subprocess.run(["openssl", "version"],
                                      capture_output=True,
                                      env=os.environ).stdout.decode().strip()}

    async def counts():
        db = await ctx.db()
        for t in ("projects", "documents", "review_tasks"):
            info[f"count_{t}"] = await db.fetchval(f"SELECT COUNT(*) FROM {t}")
    asyncio.run(counts())
    print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_node_plan(args):
    ctx = NodeCtx()

    async def run():
        units = await select_units(ctx, args.retention_days)
        out = {"retention_days": args.retention_days,
               "blockers": await blocker_stats(ctx), "units": [],
               "total_units": 0, "total_files": 0, "total_bytes": 0}
        for u in units:
            files = await collect_unit_files(ctx, u, with_hash=False,
                                             retention_days=args.retention_days)
            out["units"].append({"type": u["type"], "key": u["key"],
                                 "last_activity": u.get("last_activity"),
                                 "files": len(files),
                                 "bytes": sum(f[3] for f in files)})
            out["total_files"] += len(files)
            out["total_bytes"] += sum(f[3] for f in files)
        out["total_units"] = len(units)
        print(json.dumps(out, ensure_ascii=False))
    asyncio.run(run())


def cmd_node_archive(args):
    ctx = NodeCtx()
    if not os.environ.get(PASS_ENV):
        sys.exit(f"缺环境变量 {PASS_ENV}")
    batch_id = args.batch_id or uuid.uuid4().hex[:12]
    batch_dir = ctx.staging / batch_id
    if batch_dir.exists():
        sys.exit(f"批次目录已存在: {batch_dir}")
    batch_dir.mkdir(parents=True)
    os.chmod(batch_dir, 0o700)
    audit = open(batch_dir / "audit.jsonl", "a", encoding="utf-8")
    started_ts = time.time()
    log_evt(audit, "archive_start", batch=batch_id, retention=args.retention_days)

    async def run():
        units = await select_units(ctx, args.retention_days)
        max_bytes = int(args.max_bytes) if args.max_bytes else None
        # 选批只用 stat 体积（不读内容不做哈希），凑满上限即停止扫描；
        # 只有入选单元才在打包阶段读内容做哈希——300G workspace 选 2G 的批
        # 不再需要全量读盘（v1.4.0 及之前每批全量哈希，曾致"卡"43 分钟）。
        picked, picked_bytes = [], 0
        scanned_units = scanned_bytes = 0
        log_evt(audit, "scan_begin", candidates=len(units))
        for u in units:  # 已按 last_activity 升序（最老优先）
            if max_bytes is not None and picked_bytes >= max_bytes:
                break
            files_meta = await collect_unit_files(ctx, u, with_hash=False,
                                                  retention_days=args.retention_days)
            scanned_units += 1
            scanned_bytes += sum(f[3] for f in files_meta)
            if scanned_units % 25 == 0:
                log_evt(audit, "scan_progress", units=scanned_units,
                        bytes_seen=scanned_bytes)
            if not files_meta:
                continue
            b = sum(f[3] for f in files_meta)
            if max_bytes is not None and picked and picked_bytes + b > max_bytes:
                continue
            picked.append(u)
            picked_bytes += b
        log_evt(audit, "scan_done", scanned_units=scanned_units,
                bytes_seen=scanned_bytes, picked=len(picked),
                picked_bytes=picked_bytes)
        if not picked:
            print(json.dumps({"batch_id": batch_id, "units": [],
                              "note": "无可归档单元"}))
            return
        need = picked_bytes * 2  # tar+密文共存峰值 + 余量
        if shutil.disk_usage(ctx.staging).free < need:
            sys.exit(f"暂存盘空间不足（峰值需≈{fmt_bytes(need)}）")

        manifest = {"version": 1, "batch_id": batch_id,
                    "created_at": now_iso(), "archive_started_ts": started_ts,
                    "script_version": VERSION, "retention_days": args.retention_days,
                    "hostname": socket.gethostname(),
                    "workspace_root": str(ctx.ws_root),
                    "units": [], "total_bytes": 0, "total_files": 0}
        for u in picked:
            # 打包阶段才读内容做哈希（只为入选单元付出读盘成本）
            files = await collect_unit_files(ctx, u,
                                             retention_days=args.retention_days)
            if not files:
                continue  # 快照间隙文件消失，跳过留给下一批
            unit_id = uuid.uuid4().hex[:12]
            enc_path = batch_dir / f"{unit_id}.tar.enc"
            with tempfile.TemporaryDirectory(dir=batch_dir) as td:
                tdp = Path(td)
                tar_path = tdp / "unit.tar"
                added = []
                with tarfile.open(tar_path, "w") as tf:
                    for rp, rel, h, sz in files:
                        try:
                            tf.add(str(rp), arcname=rel, recursive=False)
                            added.append({"rel": rel, "sha256": h, "size": sz})
                        except OSError as e:
                            log_evt(audit, "archive_skip_file", rel=rel,
                                    error=str(e))
                    inner = {"version": 1, "batch_id": batch_id,
                             "unit_id": unit_id, "unit": manifest_unit(u),
                             "archive_started_ts": started_ts,
                             "workspace_root": str(ctx.ws_root), "files": added}
                    meta = tdp / INNER_DIR
                    meta.mkdir()
                    (meta / "manifest.json").write_text(
                        json.dumps(inner, ensure_ascii=False, indent=1),
                        encoding="utf-8")
                    (meta / "db_snapshot.json").write_text(
                        json.dumps(await db_snapshot(ctx, u),
                                   ensure_ascii=False, indent=1),
                        encoding="utf-8")
                    tf.add(str(meta / "manifest.json"),
                           arcname=f"{INNER_DIR}/manifest.json", recursive=False)
                    tf.add(str(meta / "db_snapshot.json"),
                           arcname=f"{INNER_DIR}/db_snapshot.json", recursive=False)
                subprocess.run(
                    openssl_args("-e") + ["-in", str(tar_path), "-out", str(enc_path)],
                    env={**os.environ}, check=True)
            if not added:
                enc_path.unlink(missing_ok=True)
                continue
            enc_sha = sha256_file(enc_path)
            manifest["units"].append(
                {"unit_id": unit_id, "type": u["type"], "key": u["key"],
                 "archive": enc_path.name, "enc_sha256": enc_sha,
                 "size": enc_path.stat().st_size, "files": len(added),
                 "bytes": sum(x["size"] for x in added),
                 "last_activity": u.get("last_activity")})
            log_evt(audit, "unit_archived", unit_id=unit_id, type=u["type"],
                    files=len(added), sha256=enc_sha[:16])
        if not manifest["units"]:
            print(json.dumps({"batch_id": batch_id, "units": [],
                              "note": "无可归档单元"}))
            return
        manifest["total_bytes"] = sum(x["bytes"] for x in manifest["units"])
        manifest["total_files"] = sum(x["files"] for x in manifest["units"])
        (batch_dir / "batch_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        log_evt(audit, "archive_done", batch=batch_id,
                units=len(manifest["units"]), bytes=manifest["total_bytes"])
        print(json.dumps({"batch_id": batch_id, "staging": str(batch_dir),
                          "units": len(manifest["units"]),
                          "total_files": manifest["total_files"],
                          "total_bytes": manifest["total_bytes"]},
                         ensure_ascii=False))
    try:
        asyncio.run(run())
    finally:
        audit.close()


def manifest_unit(u):
    return {k: u[k] for k in ("type", "key", "user_id", "last_activity")
            if u.get(k) is not None}


def prune_empty_dirs(ctx, top: Path):
    """自底向上删除空目录，但绝不越过 workspace 根。"""
    top = top.resolve()
    if top == ctx.ws_root or ctx.ws_root not in top.parents:
        return
    for root, _files, _dirs in os.walk(top, topdown=False):
        r = Path(root)
        if r == ctx.ws_root:
            continue
        try:
            if not any(r.iterdir()):
                r.rmdir()
        except OSError:
            pass
    try:
        if top != ctx.ws_root and not any(top.iterdir()):
            top.rmdir()
    except OSError:
        pass


async def clean_caches(ctx, days):
    """可再生缓存（查重/OCR）里超过保留期的文件直接删、不备份。"""
    n = b = 0
    cutoff = time.time() - days * 86400
    for name in CACHES:
        d = ctx.ws_root / name
        if not d.is_dir():
            continue
        for root, _dirs, fnames in os.walk(d):
            for fn in fnames:
                p = Path(root) / fn
                try:
                    st = p.stat()
                    if st.st_mtime < cutoff:
                        b += st.st_size
                        p.unlink()
                        n += 1
                except OSError:
                    pass
    return n, b


def receipt_unit_ok(receipt, unit):
    return any(ru.get("unit_id") == unit["unit_id"] and ru.get("ok")
               for ru in receipt.get("units", []))


def cmd_node_purge(args):
    ctx = NodeCtx()
    batch_dir = ctx.staging / args.batch_id
    manifest_path = batch_dir / "batch_manifest.json"
    if not manifest_path.exists():
        sys.exit(f"批次不存在: {batch_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt_path = batch_dir / "receipt.json"
    if not receipt_path.exists():
        sys.exit("缺 receipt.json（先在本地验证并回传，见 runbook）")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not receipt.get("ok") or receipt.get("batch_id") != manifest["batch_id"]:
        sys.exit("receipt 校验未通过，拒绝删除")
    # 门 2：暂存包 sha256 三方一致（manifest == receipt == 实际文件）
    for u in manifest["units"]:
        enc = batch_dir / u["archive"]
        if not enc.exists() or sha256_file(enc) != u["enc_sha256"] \
                or not receipt_unit_ok(receipt, u):
            sys.exit(f"暂存包校验失败: {u['archive']}")
    audit = open(batch_dir / "audit.jsonl", "a", encoding="utf-8")
    log_evt(audit, "purge_start", batch=args.batch_id)
    start_ts = manifest["archive_started_ts"]
    retention = manifest["retention_days"]

    async def run():
        # 门 3：逐单元活跃复查（归档后用户新起任务则跳过该单元）
        eligible = {u["type"] + ":" + u["key"]
                    for u in await select_units(ctx, retention)}
        result = {"batch_id": args.batch_id, "units": [], "files_deleted": 0,
                  "bytes_freed": 0, "cache_files_deleted": 0, "cache_bytes_freed": 0,
                  "skipped": []}
        db = await ctx.db()
        for u in manifest["units"]:
            tag = u["type"] + ":" + u["key"]
            if tag not in eligible:
                result["skipped"].append({"unit": tag, "reason": "重新活跃"})
                log_evt(audit, "unit_skip", unit=u["unit_id"], reason="active_again")
                continue
            unit = {"type": u["type"], "key": u["key"]}
            if u["type"] == "project":
                unit["user_id"] = await db.fetchval(
                    "SELECT user_id FROM projects WHERE id = $1", u["key"])
                if unit["user_id"] is None:
                    result["skipped"].append({"unit": tag, "reason": "DB 行已消失"})
                    continue
            elif u["type"] == "sweep":
                unit["user_id"] = u["key"]
            files = await collect_unit_files(ctx, unit, with_hash=False,
                                             archive_start_ts=start_ts,
                                             retention_days=retention)
            fb = 0
            for rp, rel, _h, sz in files:
                try:
                    ctx.fence(rp, "删除")
                    rp.unlink()
                    fb += sz
                    result["files_deleted"] += 1
                except FileNotFoundError:
                    pass
                except OSError as e:
                    log_evt(audit, "delete_error", path=rel, error=str(e))
            if u["type"] == "project":
                prune_empty_dirs(ctx, ctx.ws_root / unit["user_id"] / u["key"])
                for wid in await db.fetch(
                        "SELECT id FROM bid_wizards WHERE project_id = $1", u["key"]):
                    prune_empty_dirs(ctx, ctx.ws_root / "bid-wizard" / wid["id"])
            elif u["type"] == "blind_check":
                prune_empty_dirs(ctx, ctx.ws_root / "blind-check" / u["key"])
            result["units"].append({"unit_id": u["unit_id"], "files": len(files),
                                    "bytes": fb})
            log_evt(audit, "unit_purged", unit=u["unit_id"], files=len(files))
        cn, cb = await clean_caches(ctx, retention)
        result["cache_files_deleted"] = cn
        result["cache_bytes_freed"] = cb
        (batch_dir / "purge_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        log_evt(audit, "purge_done", batch=args.batch_id,
                files=result["files_deleted"],
                bytes=result["bytes_freed"] + cb)
        print(json.dumps(result, ensure_ascii=False))
    try:
        asyncio.run(run())
    finally:
        audit.close()


def cmd_node_extract(args):
    """恢复：把（本地解密后的）明文 tar 按内层 manifest 回抽到 workspace。"""
    ctx = NodeCtx()
    tar_path = Path(args.tar).resolve()
    with tarfile.open(tar_path) as tf:
        m = tf.extractfile(f"{INNER_DIR}/manifest.json")
        if m is None:
            sys.exit("tar 缺内层 manifest")
        inner = json.loads(m.read().decode("utf-8"))
        restored = skipped = 0
        for f in inner["files"]:
            rel = f["rel"]
            dest = ctx.ws_root / rel
            try:
                ctx.fence(dest, "回抽")
            except SystemExit:
                skipped += 1
                continue
            src = tf.extractfile(rel)
            if src is None:
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            with open(dest, "wb") as out:
                while True:
                    b = src.read(1024 * 1024)
                    if not b:
                        break
                    h.update(b)
                    out.write(b)
            if h.hexdigest() != f["sha256"]:
                dest.unlink(missing_ok=True)
                skipped += 1
                print(f"哈希不符已丢弃: {rel}", file=sys.stderr)
            else:
                restored += 1
    print(json.dumps({"unit": inner.get("unit"), "restored": restored,
                      "skipped": skipped}, ensure_ascii=False))


def cmd_node_cleanup(args):
    ctx = NodeCtx()
    cutoff = time.time() - args.older_than_days * 86400
    removed = []
    for d in sorted(ctx.staging.iterdir()):
        if not d.is_dir():
            continue
        m = d / "batch_manifest.json"
        try:
            ts = m.stat().st_mtime if m.exists() else d.stat().st_mtime
        except OSError:
            continue
        if ts < cutoff:
            shutil.rmtree(d)
            removed.append(d.name)
    print(json.dumps({"removed": removed}, ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════════
# 本地侧：向导 / 拉回 / 验证 / 回放
# ══════════════════════════════════════════════════════════════════════════

def ask(prompt, default=None):
    suffix = f"［回车={default}］" if default is not None else ""
    v = input(f"{prompt}{suffix}: ").strip()
    return v or default


def ask_yes(prompt, assume_yes=False):
    if assume_yes:
        return True
    return input(f"{prompt} [y/N]: ").strip().lower() == "y"


def load_cfg():
    if LOCAL_CFG.exists():
        try:
            return json.loads(LOCAL_CFG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cfg(cfg):
    LOCAL_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                         encoding="utf-8")


def ssh_probe(target):
    """连通性探测：真实建立连接并跑一条 echo，成功返回 (True, '')。"""
    try:
        ssh_call(target, ["echo __SSH_OK__"])
        return True, ""
    except Exception as e:
        reset_remote()
        return False, str(e)


def ensure_ssh(target, cfg, assume_yes=False):
    """向导连接保障：先按当前配置探测；失败则交互式收集 主机/用户/跳板/私钥。

    收集结果记忆到本地 cfg（密码永不记忆，每次运行时输入一次）。
    默认值即 TARGETS 里的生产实际值，新机器一路回车即可。
    """
    for attempt in range(3):
        ok, err = ssh_probe(target)
        if ok:
            return
        print(f"\n!! 节点连接失败（第 {attempt + 1} 次）：{err.splitlines()[0]}")
        if assume_yes:
            sys.exit("非交互模式连接失败：请检查 VPN/账号，或用 "
                     "--ssh/--jump/--identity 直连参数（见 24 号 runbook §四）")
        t = TARGETS[target]
        print("交互式配置连接信息（直接回车=采用［］内默认值；跳板/私钥可留空，"
              "认证默认用户名+密码，密码稍后提示输入）：")
        host = ask("目标主机", t["host"])
        user = ask("目标用户", t["user"])
        jump = ask("跳板 user@host（经 prod 内网跳转，无则留空）",
                   f"{t.get('jump_user') or user}@{t['jump']}" if t.get("jump")
                   else None) or ""
        ident = ask("私钥路径（改用密钥登录时填；留空=密码登录）", None) or ""
        ju, jh = _parse_userhost(jump) if jump else (None, None)
        t["host"], t["user"] = host, user
        t["jump"], t["jump_user"] = jh or None, ju or user
        t["identity"] = str(Path(ident).expanduser()) if ident else None
        cfg[f"ssh_{target}"] = {"host": host, "user": user, "jump": jh or "",
                                "jump_user": ju or user, "identity": ident}
        reset_remote()
        save_cfg(cfg)
        print(f"已保存连接配置到 {LOCAL_CFG}（下次直接使用；密码不保存）。")
    sys.exit("连续 3 次无法连通节点：请确认已连 VPN、账号密码正确，"
             "参见 24 号 runbook §四")


PULL_CHUNK_MB = 32  # 分块上限：2026-11-04 实测 VPN 每几分钟闪断，32MB 块约 3-5s 稳定窗口即可


def pull_batch(target, batch_id, local_dir: Path):
    """分块拉回批次目录（扁平：batch_manifest + audit + 各单元 .tar.enc）。

    实测（2026-11-04）本机→跳板→node1 的单条 430MB 长流两次被掐在 ~136MB
    （VPN 每几分钟闪断一次）——改为：文件按 64MB 块 dd 拉取、追加写+块内
    断点重来，每块独立重试（重建 SSH 连接 + 退避）。把"需要连续稳定几分钟"
    变成"多个短窗口"，闪断间隙也能推进。
    """
    if local_dir.exists():
        if (local_dir / "batch_manifest.json").exists():
            raise RuntimeError(f"本地批次目录已存在且完整，勿重复拉回: {local_dir}")
        shutil.rmtree(local_dir, ignore_errors=True)  # 上次中断的半截目录
    local_dir.mkdir(parents=True)
    staging = TARGETS[target]["staging"]
    rdir = staging + "/" + batch_id

    def remote_retry(fn, what):
        # 2026-11-04 实测 VPN 断流窗口可达数分钟：15 次 × 退避(10s→60s 封顶)
        # ≈ 覆盖 12 分钟级断流；块小、断点续传，恢复即推进。
        last = None
        for attempt in range(1, 16):
            try:
                return fn()
            except Exception as e:
                last = e
                print(f"  {what} 中断（第 {attempt}/15 次）：{e}")
                if attempt < 15:
                    reset_remote()
                    time.sleep(min(10 * attempt, 60))
                    print("  已重建连接，重试…")
        raise RuntimeError(f"{what} 连续 15 次失败（生产文件未动，批次仍在节点暂存区）: {last}")

    def list_files():
        r = get_remote(target).run(
            "set -euo pipefail\n"
            f"find {shlex.quote(rdir)} -maxdepth 1 -type f -printf '%s %f\\n' | sort -k2\n")
        out = []
        for ln in r.stdout.decode("utf-8").splitlines():
            sz, name = ln.split(None, 1)
            if name.startswith("/") or ".." in Path(name).parts or Path(name).name != name:
                raise RuntimeError(f"可疑文件名: {name}")
            out.append((name, int(sz)))
        return out

    files = remote_retry(list_files, "列取批次文件")
    chunk = PULL_CHUNK_MB * 1024 * 1024
    for name, size in files:
        dst = local_dir / name
        dst.touch(exist_ok=True)
        off, parts = 0, (size + chunk - 1) // chunk
        while off < size:
            take = min(chunk, size - off)
            skip = off // (4 * 1024 * 1024)
            cnt = (take + 4 * 1024 * 1024 - 1) // (4 * 1024 * 1024)

            def pull_one(off=off, take=take, skip=skip, cnt=cnt, name=name, dst=dst):
                with open(dst, "r+b") as f:
                    f.seek(off)
                    f.truncate()  # 块内断点一律整块重来
                    n = get_remote(target).run_stream_out(
                        "set -euo pipefail\n"
                        f"exec dd if={shlex.quote(rdir + '/' + name)} bs=4M"
                        f" skip={skip} count={cnt} iflag=fullblock status=none\n", f)
                if n != take:
                    raise RuntimeError(f"块长度不符: 期望 {take} 实得 {n}")
                return n

            remote_retry(pull_one, f"拉回 {name} @ {fmt_bytes(off)}")
            off += take
        got = dst.stat().st_size
        if got != size:
            raise RuntimeError(f"{name} 尺寸不符: 期望 {size} 实得 {got}")
        print(f"  已拉回 {name}（{fmt_bytes(size)}）")
    if not (local_dir / "batch_manifest.json").exists():
        raise RuntimeError("拉回内容异常：缺 batch_manifest.json")


def openssl_local(op, infile, outfile, passphrase):
    env = {**os.environ, PASS_ENV: passphrase}
    return subprocess.run(
        openssl_args(op, exe=find_openssl())
        + ["-in", str(infile), "-out", str(outfile)],
        env=env, capture_output=True)


def verify_batch(local_dir: Path, passphrase, extract_to=None):
    """本地验证：sha256 比对 + 解密 + tar 完整性 + 逐文件哈希。

    extract_to 不为 None 时，验证通过的单元顺带把解密文件落到
    extract_to/<unit_id>/（同一解密 pass，不二次解密；明文，看完即删）。
    """
    manifest = json.loads((local_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    receipt = {"batch_id": manifest["batch_id"], "verified_at": now_iso(),
               "script_version": VERSION, "ok": True, "units": []}
    for u in manifest["units"]:
        enc = local_dir / u["archive"]
        entry = {"unit_id": u["unit_id"], "ok": False}
        try:
            if not enc.exists() or sha256_file(enc) != u["enc_sha256"]:
                raise RuntimeError("加密包缺失或 sha256 不符")
            with tempfile.TemporaryDirectory(dir=str(local_dir)) as td:
                # 临时解密目录放在批次同盘（不占 C 盘 %TEMP%），用完即清
                tar_path = Path(td) / "u.tar"
                r = openssl_local("-d", enc, tar_path, passphrase)
                if r.returncode != 0:
                    raise RuntimeError("解密失败（口令错误？）")
                with tarfile.open(tar_path) as tf:
                    mf = tf.extractfile(f"{INNER_DIR}/manifest.json")
                    if mf is None:
                        raise RuntimeError("缺内层 manifest")
                    inner = json.loads(mf.read().decode("utf-8"))
                    listed = {f["rel"]: f["sha256"] for f in inner["files"]}
                    members = {m.name for m in tf.getmembers()
                               if m.isfile() and not m.name.startswith(INNER_DIR + "/")}
                    for rel, want in listed.items():
                        src = tf.extractfile(rel)
                        if src is None:
                            raise RuntimeError(f"tar 缺文件 {rel}")
                        if sha256_stream(src) != want:
                            raise RuntimeError(f"哈希不符 {rel}")
                    extra = members - set(listed)
                    if extra:
                        raise RuntimeError(f"tar 含未登记文件 {len(extra)} 个")
                    if extract_to is not None:
                        udir = extract_to / u["unit_id"]
                        n_ext = 0
                        for m in tf.getmembers():
                            rel = PurePosixPath(m.name)
                            # 路径围栏：拒绝绝对路径/../盘符，防 tar 内可疑成员逃逸
                            if rel.is_absolute() or ".." in rel.parts \
                                    or any(":" in part for part in rel.parts):
                                raise RuntimeError(f"可疑成员路径 {m.name}")
                            dst = udir.joinpath(*rel.parts)
                            if m.isdir():
                                dst.mkdir(parents=True, exist_ok=True)
                                continue
                            if not m.isfile():
                                continue
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            src = tf.extractfile(m)
                            with open(dst, "wb") as out:
                                shutil.copyfileobj(src, out)
                            n_ext += 1
                        entry["files_extracted"] = n_ext
                        entry["extract_dir"] = str(udir)
            entry.update(ok=True, enc_sha256=u["enc_sha256"],
                         files_checked=u["files"])
        except Exception as e:
            entry["error"] = str(e)
            receipt["ok"] = False
        receipt["units"].append(entry)
    (local_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    return receipt


def show_plan(plan):
    print(f"\n── 可归档（保留期 {plan['retention_days']} 天）──")
    agg = {}
    for u in plan["units"]:
        a = agg.setdefault(u["type"], [0, 0, 0])
        a[0] += 1
        a[1] += u["files"]
        a[2] += u["bytes"]
    for t, (n, f, b) in agg.items():
        print(f"  {t:<12} {n:>5} 单元  {f:>7} 文件  {fmt_bytes(b)}")
    print(f"  合计         {plan['total_units']:>5} 单元  {plan['total_files']:>7} 文件"
          f"  {fmt_bytes(plan['total_bytes'])}")
    names = {"review_active": "审查任务进行中", "review_unsettled": "审查计费未结算",
             "writing_active": "AI编标撰写进行中", "draft_active": "标书生成进行中",
             "wizard_active": "AI编标向导进行中", "doc_parsing": "文档解析中"}
    blocked = {names[k]: v for k, v in plan["blockers"].items() if v}
    if blocked:
        print("── 暂不满足条件（阻塞统计，按项目数）──")
        for k, v in blocked.items():
            print(f"  {k}: {v}")


def get_passphrase():
    pw = os.environ.get(PASS_ENV)
    if pw:
        return pw
    for _ in range(3):
        a = getpass.getpass("归档口令（不会显示，务必牢记并离线保存）: ")
        b = getpass.getpass("再输一遍确认: ")
        if a and a == b:
            return a
        print("两次输入不一致，重试。")
    sys.exit("口令输入失败")


def cmd_run(args):
    cfg = load_cfg()
    target = args.target
    apply_saved_ssh(cfg, target)
    apply_ssh_overrides(args)
    print(f"== bjt-agent 标书归档下线 v{VERSION} ==")
    print(f"目标: {TARGETS[target]['desc']}")
    archive_root = Path(args.archive_root or cfg.get("archive_root") or
                        ask("本地归档根目录", "E:\\bjt-archive"))
    archive_root.mkdir(parents=True, exist_ok=True)
    cfg["archive_root"] = str(archive_root)
    save_cfg(cfg)
    staging = TARGETS[target]["staging"]

    ensure_ssh(target, cfg, args.yes)  # 连不通自动进入交互式收集（记忆到本地 cfg）

    print("\n[1/6] 检查节点环境…")
    env = node_json(target, ["env-info"])
    print(f"  {env['hostname']}  workspace={env['workspace_root']}"
          f"  暂存可用 {fmt_bytes(env['staging_free'])}  {env['openssl']}")

    print("\n[2/6] 生成归档计划（只读）…")
    plan = node_json(target, ["plan", "--retention-days", str(args.retention_days)])
    if not plan["units"]:
        print("没有可归档单元，结束。")
        return
    show_plan(plan)
    if args.plan_only:
        return

    max_gb = args.max_gb
    if max_gb is None and plan["total_bytes"] > 20 * 1024 ** 3:
        print("总量超过 20G，建议分批。")
        max_gb = float(ask("本批上限 GB", "10"))
    passphrase = get_passphrase()
    if not ask_yes(f"\n确认开始归档（远端打包加密 → 拉回 {archive_root}）？", args.yes):
        print("已取消。")
        return

    print("\n[3/6] 远端打包加密…")
    na = ["archive", "--retention-days", str(args.retention_days)]
    if max_gb:
        na += ["--max-bytes", str(int(max_gb * 1024 ** 3))]
    summary = node_json(target, na, pass_env={PASS_ENV: passphrase})
    batch_id = summary["batch_id"]
    if not summary.get("units"):
        print("本次实际无可归档单元（文件可能已被清理）。")
        return
    print(f"  批次 {batch_id}: {summary['units']} 单元 / "
          f"{summary['total_files']} 文件 / {fmt_bytes(summary['total_bytes'])}")
    # 拉回前本地磁盘预检：需求≈批次体积 1.1 倍（拉回 + 验证时最大单元临时解密）
    need = int(summary["total_bytes"] * 1.1) + 512 * 1024 * 1024
    free = shutil.disk_usage(archive_root).free
    if free < need:
        print(f"!! 本地归档盘剩余 {fmt_bytes(free)}，不足本批需求≈{fmt_bytes(need)}，"
              f"已中止拉回。")
        print("   处置：换归档目录（--archive-root）或 --max-gb 调小分批；"
              "节点暂存区已生成的本批加密包按 runbook §五 清理或留作补拉。")
        return
    local_dir = archive_root / batch_id
    print(f"\n[4/6] 拉回到 {local_dir} …")
    pull_batch(target, batch_id, local_dir)
    logs_dir = archive_root / "logs"
    logs_dir.mkdir(exist_ok=True)

    print("\n[5/6] 本地解密验证…")
    receipt = verify_batch(local_dir, passphrase)
    ok_units = sum(1 for u in receipt["units"] if u["ok"])
    print(f"  验证通过 {ok_units}/{len(receipt['units'])} 单元  整体 ok={receipt['ok']}")
    (logs_dir / f"{batch_id}.log").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    if not receipt["ok"]:
        print("!! 验证未通过，不删除生产文件。排查口令/网络后删除本地残缺目录重跑。")
        return
    cfg["last_batch"] = batch_id
    save_cfg(cfg)

    if args.no_purge:
        print("\n--no-purge：已归档未删除。手工删除方法见 runbook §5。")
        return
    if not ask_yes("验证通过。确认从生产删除这些文件？（恢复须用本地归档包）",
                   args.yes):
        print("已保留生产文件（加密包暂存区与本地各一份）。")
        return
    print("\n[6/6] 远端删除（三重门校验）…")
    scp_to(target, local_dir / "receipt.json", f"{staging}/{batch_id}")
    result = node_json(target, ["purge", "--batch-id", batch_id])
    print(f"  删除 {result['files_deleted']} 文件，释放 "
          f"{fmt_bytes(result['bytes_freed'] + result['cache_bytes_freed'])}"
          f"（含缓存 {result['cache_files_deleted']} 个）")
    for s in result.get("skipped", []):
        print(f"  跳过: {s['unit']}（{s['reason']}）")
    (logs_dir / f"{batch_id}.log").write_text(
        json.dumps({"receipt": receipt, "purge": result},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完成。批次 {batch_id}。请立即做第二副本（移动硬盘/NAS），"
          f"并在 24 号 runbook 登记本批次。")
    print(f"节点暂存区加密包保留 {DEFAULT_STAGING_KEEP_DAYS} 天，"
          f"过期用 cleanup-staging 清理。")


def cmd_plan(args):
    apply_ssh_overrides(args)
    plan = node_json(args.target,
                     ["plan", "--retention-days", str(args.retention_days)])
    if plan["units"]:
        show_plan(plan)
    else:
        print("没有可归档单元。阻塞统计:",
              json.dumps(plan["blockers"], ensure_ascii=False))


def cmd_verify(args):
    extract_to = Path(args.extract_to) if args.extract_to else None
    if extract_to is not None:
        extract_to.mkdir(parents=True, exist_ok=True)
    receipt = verify_batch(Path(args.batch_dir),
                           os.environ.get(PASS_ENV) or getpass.getpass("归档口令: "),
                           extract_to=extract_to)
    print(json.dumps(receipt, ensure_ascii=False, indent=1))
    if extract_to is not None and receipt["ok"]:
        n = sum(1 for e in receipt["units"] if e.get("files_extracted"))
        print(f"\n已解密落盘 {n} 个单元 → {extract_to}\\<unit_id>\\"
              "\n⚠️ 明文目录，看完即删；批次目录本身保持加密态。")
    sys.exit(0 if receipt["ok"] else 1)


def cmd_restore(args):
    cfg = load_cfg()
    apply_saved_ssh(cfg, args.target)
    apply_ssh_overrides(args)
    ensure_ssh(args.target, cfg)
    root = Path(args.archive_root or cfg.get("archive_root") or
                ask("本地归档根目录"))
    batches = sorted(p for p in root.iterdir()
                     if p.is_dir() and (p / "batch_manifest.json").exists())
    if not batches:
        sys.exit("归档根下没有批次")
    for i, b in enumerate(batches):
        m = json.loads((b / "batch_manifest.json").read_text(encoding="utf-8"))
        print(f"  [{i}] {b.name}  {m['created_at']}  "
              f"{len(m['units'])} 单元 / {fmt_bytes(m['total_bytes'])}")
    try:
        sel = 0 if len(batches) == 1 else int(ask("选择批次序号", "0") or 0)
    except ValueError:
        sys.exit("序号格式错误")
    batch_dir = batches[sel]
    manifest = json.loads(
        (batch_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    print("单元：")
    for i, u in enumerate(manifest["units"]):
        print(f"  [{i}] {u['unit_id']}  {u['type']}  {u['files']} 文件")
    picks = ask("要恢复的单元序号（逗号分隔，a=全部）", "a")
    try:
        idx = (list(range(len(manifest["units"]))) if picks == "a"
               else [int(x) for x in picks.split(",") if x.strip()])
    except ValueError:
        sys.exit("序号格式错误")
    passphrase = os.environ.get(PASS_ENV) or getpass.getpass("归档口令: ")
    target = args.target
    remote_dir = f"{TARGETS[target]['staging']}/restore_" \
                 f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    ssh_call(target, [f"mkdir -p {shlex.quote(remote_dir)}"])
    for i in idx:
        u = manifest["units"][i]
        enc = batch_dir / u["archive"]
        print(f"→ 解密并回抽 {u['unit_id']}（{u['type']}）…")
        with tempfile.TemporaryDirectory(dir=str(root)) as td:
            # 临时解密目录放归档根同盘（不占 C 盘）
            tar_path = Path(td) / f"{u['unit_id']}.tar"
            r = openssl_local("-d", enc, tar_path, passphrase)
            if r.returncode != 0:
                print("  解密失败（口令错误？）跳过该单元。")
                continue
            scp_to(target, tar_path, remote_dir)
            p = node_py(target, ["extract", "--tar",
                                 f"{remote_dir}/{tar_path.name}"])
            print("  " + p.stdout.decode("utf-8").strip())
    print(f"\n回抽完成。明文 tar 已上传至节点 {remote_dir}，"
          f"确认恢复无误后请手动清理该目录。")


def cmd_cleanup_staging(args):
    apply_ssh_overrides(args)
    res = node_json(args.target,
                    ["cleanup", "--older-than-days", str(args.older_than_days)])
    print("已清理暂存批次:", res["removed"] or "无")


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def add_ssh_opts(p):
    """连接参数（默认交互式询问并记忆；此处可直接指定跳过询问）。"""
    p.add_argument("--ssh", dest="ssh_host", metavar="USER@HOST",
                   help="目标 user@host（如 root@192.168.40.110）")
    p.add_argument("--jump", dest="ssh_jump", metavar="USER@HOST",
                   help="跳板 user@host（如 root@10.1.3.241，经 prod 内网跳转）")
    p.add_argument("--identity", dest="ssh_identity", metavar="FILE",
                   help="私钥文件路径（默认用户名+密码登录）")


def main():
    ap = argparse.ArgumentParser(
        description="bjt-agent 用户标书归档下线（备份→验证→删除）")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run", help="交互式向导（默认）")
    p.add_argument("--target", choices=list(TARGETS), default="prod")
    add_ssh_opts(p)
    p.add_argument("--archive-root")
    p.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    p.add_argument("--max-gb", type=float, default=None, help="本批体积上限")
    p.add_argument("--plan-only", action="store_true")
    p.add_argument("--no-purge", action="store_true")
    p.add_argument("--yes", action="store_true",
                   help="跳过交互确认（口令仍需输入或环境变量）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("plan", help="只读盘点")
    p.add_argument("--target", choices=list(TARGETS), default="prod")
    add_ssh_opts(p)
    p.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("verify", help="手动重验本地批次")
    p.add_argument("--batch-dir", required=True)
    p.add_argument("--extract-to", metavar="DIR",
                   help="验证通过后把解密文件落到 DIR/<unit_id>/（明文，看完即删）")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restore", help="从本地归档回放恢复到生产")
    p.add_argument("--target", choices=list(TARGETS), default="prod")
    add_ssh_opts(p)
    p.add_argument("--archive-root")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("cleanup-staging", help="清理节点暂存区过期批次")
    p.add_argument("--target", choices=list(TARGETS), default="prod")
    add_ssh_opts(p)
    p.add_argument("--older-than-days", type=int, default=DEFAULT_STAGING_KEEP_DAYS)
    p.set_defaults(func=cmd_cleanup_staging)

    np_ = sub.add_parser("__node", help="节点内部命令（由向导调起，勿直接使用）")
    nsub = np_.add_subparsers(dest="node_cmd", required=True)

    q = nsub.add_parser("env-info")
    q.set_defaults(func=cmd_node_env_info)

    q = nsub.add_parser("plan")
    q.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    q.set_defaults(func=cmd_node_plan)

    q = nsub.add_parser("archive")
    q.add_argument("--batch-id", default=None)
    q.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    q.add_argument("--max-bytes", type=int, default=None)
    q.set_defaults(func=cmd_node_archive)

    q = nsub.add_parser("purge")
    q.add_argument("--batch-id", required=True)
    q.set_defaults(func=cmd_node_purge)

    q = nsub.add_parser("extract")
    q.add_argument("--tar", required=True)
    q.set_defaults(func=cmd_node_extract)

    q = nsub.add_parser("cleanup")
    q.add_argument("--older-than-days", type=int, default=DEFAULT_STAGING_KEEP_DAYS)
    q.set_defaults(func=cmd_node_cleanup)

    args = ap.parse_args(sys.argv[1:] or ["run"])
    if getattr(args, "func", None) is None:
        ap.error("缺少子命令")
    args.func(args)


if __name__ == "__main__":
    main()
