#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bjt-agent 标书检查/查重 skill 客户端（零依赖，仅 Python 标准库）。

命令面与契约见 references/api.md（契约 v0，对应 bjt-agent 开放端点 /api/v1/open）。
凭证读取优先级：环境变量 BJT_API_KEY > skill 内 config.json。
config.json 含真实 Key，权限 600，勿上传发布包/提交仓库。

用法速查：
  python3 scripts/bjt.py login --api-key bjt_live_xxx    # 保存凭证
  python3 scripts/bjt.py me                              # 连通 + 剩余点数自检
  python3 scripts/bjt.py upload 招标文件.pdf --type tender     # 上传（自动等解析）
  python3 scripts/bjt.py documents <document_id>         # 查解析状态
  python3 scripts/bjt.py review --tender <id> --bid <id> [--wait/--no-wait]
  python3 scripts/bjt.py duplicate <left_id> <right_id>  [--wait/--no-wait]
  python3 scripts/bjt.py status <task_id>                # 单次查状态
  python3 scripts/bjt.py result <task_id>                # 取结果 + 报告链接
  python3 scripts/bjt.py cancel <task_id>
  python3 scripts/bjt.py list
"""
import argparse
import json
import os
import re
import sys
import time
import uuid
from urllib import request as _urlreq
from urllib import error as _urlerr
from urllib.parse import urlparse

SKILL_VERSION = "0.1.0"
DEFAULT_BASE = "https://check.aibjt.com:30002/api/v1/open"
DOC_TYPES = ("tender", "bid", "duplicate_left", "duplicate_right")
TASK_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
POLL_INTERVAL = 30          # 秒；任务通常数十分钟，勿更密集
POLL_TIMEOUT = 7200         # 秒
PARSE_POLL_INTERVAL = 10
PARSE_TIMEOUT = 1800


def die(msg, code=1):
    print("ERROR: %s" % msg, file=sys.stderr)
    sys.exit(code)


def log(msg):
    print(msg, file=sys.stderr)


def creds_path():
    env = os.environ.get("BJT_CONFIG")
    if env:
        return env
    return os.path.join(skill_dir(), "config.json")


def skill_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_creds():
    path = creds_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {}


def save_creds(data):
    path = creds_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def get_api_key():
    key = os.environ.get("BJT_API_KEY") or load_creds().get("api_key")
    if not key:
        _guide_missing_credentials()
        sys.exit(2)
    return key


def _guide_missing_credentials():
    print("缺少 bjt-agent API Key。获取方式：", file=sys.stderr)
    print("  1. 打开 https://check.aibjt.com:30002 注册并登录；", file=sys.stderr)
    print("  2. 进入「个人中心 → API Key（Skill 接入）」生成新 Key 并立即复制（只展示一次）；", file=sys.stderr)
    print("  3. 把 Key 粘贴到对话里让助手保存（助手代跑: login --api-key bjt_live_xxx）。", file=sys.stderr)
    print("  或临时使用环境变量：export BJT_API_KEY=bjt_live_xxx", file=sys.stderr)


def base_url():
    return (load_creds().get("base") or os.environ.get("BJT_BASE") or DEFAULT_BASE).rstrip("/")


def _headers(extra=None):
    h = {"X-Api-Key": get_api_key(), "Accept": "application/json"}
    if extra:
        h.update(extra)
    return h


def _explain_http_error(e):
    """把 HTTP 错误翻译成给助手看的一句话处理指引（不直接抛给用户）。

    服务端错误体为 house style：{"detail": {"code": ..., "message": ...}}；
    兼容回退 {"error": {...}} 形态。
    """
    body = ""
    code = getattr(e, "code", None)
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:
        pass
    err = {}
    try:
        parsed = json.loads(body) if body else {}
        err = (parsed.get("detail") if isinstance(parsed.get("detail"), dict) else None) \
            or (parsed.get("error") if isinstance(parsed.get("error"), dict) else None) \
            or {}
    except ValueError:
        pass
    biz = err.get("code", "")
    msg = err.get("message", "") or body[:300]
    hints = {
        401: "Key 无效或缺失 → 引导用户按 SKILL.md「快速开始」重新获取并保存 Key（missing_credentials/invalid_credentials）",
        402: "点数余额不足 → 引导用户到运营后台充值后再提交（INSUFFICIENT_BALANCE）",
        403: "Key 已被吊销 → 引导用户在运营后台重置 Key（key_revoked）",
        404: "句柄不存在、非本人或开放通道未开启 → 核对 document_id/task_id（not_found/task_not_found）",
        409: "已有进行中的未结算任务 → 用 list 查看进度，等完成后再提交（ACTIVE_BILLING_TASK_EXISTS/invalid_job_state）",
        413: "文件超出单份 1GiB 限制",
        422: "参数/文件校验失败：%s（含查重两份相同 identical_documents）" % msg,
        429: "触发限流 → 稍等后重试（看 Retry-After）",
    }
    hint = hints.get(code, "服务端异常，可稍后重试；云端任务用 list/status 续查")
    die("HTTP %s %s | %s" % (code, biz or msg[:80], hint))


def request_json(method, path, *, headers=None, data=None, json_body=None):
    url = base_url() + path
    body = None
    hdrs = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = data
    req = _urlreq.Request(url, data=body, headers=hdrs, method=method)
    try:
        with _urlreq.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except _urlerr.HTTPError as e:
        _explain_http_error(e)
    except _urlerr.URLError as e:
        die("网络错误：%s（稍后重试；已提交的云端任务不受影响，用 list/status 续查）" % e.reason)


def encode_multipart(fields, files):
    """fields: {name: str}; files: {name: (filename, bytes)}；返回 (body, content_type)。"""
    boundary = "----bjt%s" % uuid.uuid4().hex
    out = []
    for name, value in fields.items():
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                    % (boundary, name, value)).encode("utf-8"))
    for name, (filename, blob) in files.items():
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name, filename)).encode("utf-8"))
        out.append(blob)
        out.append(b"\r\n")
    out.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(out), "multipart/form-data; boundary=%s" % boundary


def is_url(s):
    return s.startswith("https://") or s.startswith("http://")


def download_to_local(url, max_mb=1024):
    """云端 URL 先下载到本地再上传（仅 https/公网，避免把内网地址传给服务端）。"""
    p = urlparse(url)
    if p.scheme != "https" or not p.netloc:
        die("仅支持 https 公网 URL：%s" % url, 2)
    name = os.path.basename(p.path) or "download.bin"
    dest_dir = os.path.join(skill_dir(), "bjt-files", "_downloads")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    with _urlreq.urlopen(url, timeout=300) as resp, open(dest, "wb") as f:
        total = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > max_mb << 20:
                die("下载超过 %sMB 上限：%s" % (max_mb, url), 2)
            f.write(chunk)
    return dest


def check_file(path):
    if not os.path.isfile(path):
        die("文件不存在：%s" % path, 2)
    if os.path.getsize(path) > 1 << 30:
        die("文件超过 1GiB 上限：%s" % path, 2)
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in ("pdf", "docx", "doc", "xlsx"):
        die("仅支持 pdf/docx/doc/xlsx：%s" % path, 2)
    return path


def poll(task_id, interval=POLL_INTERVAL, timeout=POLL_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = request_json("GET", "/tasks/%s" % task_id, headers=_headers())
        status = t.get("status")
        prog = t.get("progress") or {}
        if prog:
            percent = prog.get("percent")
            if percent is None:
                # 查重等任务无数值进度：展示阶段而非 None
                log("[%s] %s" % (status, prog.get("stage_label") or "执行中"))
            else:
                suffix = ""
                if prog.get("current_step") is not None and prog.get("total_steps"):
                    suffix = " · %s/%s" % (prog["current_step"], prog["total_steps"])
                log("[%s%%] %s%s" % (percent, prog.get("stage_label") or "", suffix))
        if status in ("completed", "failed", "cancelled"):
            return t
        time.sleep(interval)
    die("等待超时（%ss）。任务仍在云端执行，稍后用 status/result 续查：%s" % (timeout, task_id))


def cmd_login(args):
    data = load_creds()
    data["api_key"] = args.api_key
    if args.base:
        data["base"] = args.base
    path = save_creds(data)
    print("凭证已保存：%s" % path)


def cmd_logout(args):
    data = load_creds()
    data.pop("api_key", None)
    save_creds(data)
    print("凭证已清除。")


def cmd_me(args):
    r = request_json("GET", "/me", headers=_headers())
    print("剩余点数：%s（充值 %s / 赠送 %s）" % (
        r.get("balance_points"), r.get("recharge_points"), r.get("gift_points")))
    limits = r.get("limits") or {}
    print("限额：每分钟 %s 次请求；同时进行任务 %s/%s" % (
        limits.get("rate_per_min"), limits.get("running_tasks"), limits.get("max_active_tasks")))


def _wait_parse(document_id):
    deadline = time.time() + PARSE_TIMEOUT
    while time.time() < deadline:
        d = request_json("GET", "/documents/%s" % document_id, headers=_headers())
        status = d.get("status")
        if status == "parsed":
            return d
        if status == "failed":
            die("解析失败：%s（多为加密/损坏文件，请解密导出后重传）" % d.get("error", ""))
        log("解析中 …（%s）" % status)
        time.sleep(PARSE_POLL_INTERVAL)
    die("解析等待超时，稍后用 documents %s 查询" % document_id)


def normalize_local_path(path):
    """git-bash 风格路径（/e/foo）转 Windows 盘符路径（E:/foo）。

    WorkBuddy/CodeBuddy 的 Bash 工具在 Windows 上常传 /e/... 形式路径，
    而 Windows Python 的 os.path 不识别；其他形态原样返回。
    """
    m = re.match(r"^/([a-zA-Z])/(.+)$", path)
    if m and os.path.exists("%s:/%s" % (m.group(1).upper(), m.group(2))):
        return "%s:/%s" % (m.group(1).upper(), m.group(2))
    return path


def cmd_upload(args):
    src = args.file
    if is_url(src):
        src = download_to_local(src)
    else:
        src = check_file(normalize_local_path(os.path.expanduser(src)))
    with open(src, "rb") as f:
        body, ctype = encode_multipart(
            {"doc_type": args.type},
            {"file": (os.path.basename(src), f.read())})
    r = request_json("POST", "/documents", headers=_headers({"Content-Type": ctype}), data=body)
    document_id = r.get("document_id")
    print("已上传 document_id=%s（%s），等待解析…" % (document_id, args.type))
    if args.no_wait:
        print("解析中，稍后用 documents %s 查询" % document_id)
        return
    _wait_parse(document_id)
    print("解析完成 document_id=%s，可用于提交任务" % document_id)


def cmd_documents(args):
    d = request_json("GET", "/documents/%s" % args.document_id, headers=_headers())
    print("status=%s pages=%s %s" % (d.get("status"), d.get("pages"), d.get("error") or ""))


def _submit(path, json_body):
    headers = _headers({"Idempotency-Key": str(uuid.uuid4())})
    return request_json("POST", path, headers=headers, json_body=json_body)


def _report_task(t):
    print("task_id=%s status=%s billing=%s" % (
        t.get("task_id"), t.get("status"), t.get("billing_status")))
    if t.get("error"):
        print("error=%s | %s" % (t["error"].get("code"), t["error"].get("message")))


def cmd_review(args):
    r = _submit("/review", {
        "tender_document_ids": args.tender,
        "bid_document_ids": args.bid,
    })
    print("已提交审查任务 task_id=%s（自动轮询，Ctrl-C 不影响云端执行）" % r.get("task_id"))
    if args.no_wait:
        return
    t = poll(r.get("task_id"))
    _report_task(t)
    if t.get("status") == "completed":
        _print_result(request_json("GET", "/tasks/%s/result" % r["task_id"], headers=_headers()))


def cmd_duplicate(args):
    if args.left == args.right:
        die("两份文件 document_id 相同：请确认上传的是两份不同的投标文件", 2)
    r = _submit("/duplicate-check", {
        "left_document_id": args.left,
        "right_document_id": args.right,
    })
    print("已提交查重任务 task_id=%s（自动轮询，Ctrl-C 不影响云端执行）" % r.get("task_id"))
    if args.no_wait:
        return
    t = poll(r.get("task_id"))
    _report_task(t)
    if t.get("status") == "completed":
        _print_result(request_json("GET", "/tasks/%s/result" % r["task_id"], headers=_headers()))


def _print_result(r):
    result = r.get("result") or {}
    if r.get("service") == "review":
        summary = result.get("summary") or {}
        conclusion = summary.get("conclusion")
        if conclusion:
            print("审查结论：%s" % conclusion)
        else:
            print("审查结论：见问题清单与报告（总体报告未生成结论段）")
        print("高风险 %s · 待复核 %s · 提示 %s" % (
            summary.get("high_count"), summary.get("review_count"), summary.get("tip_count")))
        for issue in (result.get("issues") or [])[:10]:
            print("- [%s] %s" % (issue.get("risk_level"), issue.get("title")))
        print("（完整清单与依据见报告）")
    elif r.get("service") == "duplicate":
        print("判定：%s（reasonable=无明显雷同 / suspicious=存在雷同风险）" % result.get("verdict"))
        print("相似度分数：%s" % result.get("similarity_score"))
        for ev in (result.get("evidences") or [])[:5]:
            # 服务端结构：left/right 两侧的 location+excerpt（见 references/api.md）
            loc = ev.get("left_location") or {}
            loc_text = loc.get("section") or loc.get("page") or ""
            print("- A侧 %s：%s" % (loc_text, (ev.get("left_excerpt") or "")[:80]))
            loc_r = ev.get("right_location") or {}
            loc_r_text = loc_r.get("section") or loc_r.get("page") or ""
            print("  B侧 %s：%s" % (loc_r_text, (ev.get("right_excerpt") or "")[:80]))
    if r.get("report_url"):
        print("报告链接（有时效，请及时查看/下载）：%s" % r["report_url"])


def cmd_status(args):
    _report_task(request_json("GET", "/tasks/%s" % args.task_id, headers=_headers()))


def cmd_result(args):
    _print_result(request_json("GET", "/tasks/%s/result" % args.task_id, headers=_headers()))


def cmd_cancel(args):
    request_json("POST", "/tasks/%s/cancel" % args.task_id, headers=_headers(), json_body={})
    print("已请求取消 %s（注意：取消的任务也可能产生费用，见 billing.md）" % args.task_id)


def cmd_list(args):
    r = request_json("GET", "/tasks?page=%s" % (args.page or 1), headers=_headers())
    for t in (r.get("tasks") or []):
        print("%s  %-10s %-9s %s" % (
            t.get("task_id"), t.get("service"), t.get("status"), t.get("title") or ""))


def build_parser():
    p = argparse.ArgumentParser(prog="bjt.py", description="bjt-agent 标书检查/查重客户端 v%s" % SKILL_VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("login", help="保存 API Key 到 skill 内 config.json")
    sp.add_argument("--api-key", required=True)
    sp.add_argument("--base", help="覆盖服务地址（默认生产）")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("logout", help="清除本地凭证")
    sp.set_defaults(func=cmd_logout)

    sp = sub.add_parser("me", help="连通性 + 剩余点数自检")
    sp.set_defaults(func=cmd_me)

    sp = sub.add_parser("upload", help="上传并解析文档")
    sp.add_argument("file", help="本地路径或 https URL")
    sp.add_argument("--type", required=True, choices=DOC_TYPES)
    sp.add_argument("--no-wait", action="store_true", help="不等解析完成")
    sp.set_defaults(func=cmd_upload)

    sp = sub.add_parser("documents", help="查询解析状态")
    sp.add_argument("document_id")
    sp.set_defaults(func=cmd_documents)

    sp = sub.add_parser("review", help="提交标书审查任务")
    sp.add_argument("--tender", action="append", required=True, help="招标文件 document_id（可多次）")
    sp.add_argument("--bid", action="append", required=True, help="投标文件 document_id（可多次）")
    sp.add_argument("--no-wait", action="store_true")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("duplicate", help="提交标书查重任务（两份不同投标文件）")
    sp.add_argument("left")
    sp.add_argument("right")
    sp.add_argument("--no-wait", action="store_true")
    sp.set_defaults(func=cmd_duplicate)

    sp = sub.add_parser("status", help="单次查询任务状态")
    sp.add_argument("task_id")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("result", help="取任务结果与报告链接")
    sp.add_argument("task_id")
    sp.set_defaults(func=cmd_result)

    sp = sub.add_parser("cancel", help="取消任务（可能仍计费）")
    sp.add_argument("task_id")
    sp.set_defaults(func=cmd_cancel)

    sp = sub.add_parser("list", help="最近任务列表（断点续查用）")
    sp.add_argument("--page", type=int, default=1)
    sp.set_defaults(func=cmd_list)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
