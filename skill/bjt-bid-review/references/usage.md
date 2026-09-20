# 执行细节（助手操作手册）

本文件是**执行任务时的完整操作指引**——上传、提交、轮询、取结果、处理边界情形时按需查阅。接口契约见 [api.md](api.md)，计费口径见 [billing.md](billing.md)。

## ⚠️ 输出约定

1. **进度照常显示**：`review`/`duplicate` 会把百分比+阶段（如 `[47%] 规则检查`）打到 stderr——不要 `2>` 重定向吞掉。
2. **产物交付**：每次结果产出后，把结论摘要 + **报告链接**明确告知用户；链接有时效（默认 7 天内有效，短时签名），提醒及时查看/下载。
3. **凭证类报错是给助手看的**：`login` 缺参、`非交互环境`、exit 2 这类提示不要原样抛给用户——翻译成「下一步请提供什么」，由你代跑命令。

## 凭证（第 1 步）

凭证默认存在 **skill 内 `config.json`**。读取优先级：环境变量 `BJT_API_KEY` > `config.json`。

```bash
python3 scripts/bjt.py login --api-key bjt_live_xxx   # 用户把 Key 发你后，由你代跑保存
python3 scripts/bjt.py me                             # 连通 + 剩余点数自检
```

- **保存后把 config.json 完整路径转述给用户**。
- 🔒 `config.json` 含真实 Key——**绝不上传发布包/提交仓库**；发布包不含任何配置文件。
- 用户没有任何 Key：引导到 https://check.aibjt.com:30002 注册登录 → 「个人中心 → API Key（Skill 接入）」页签生成复制（完整 Key 只展示一次，提醒用户立即保存）→ 粘贴到对话，由你代跑 `login` 保存。**别把命令丢给用户自己敲。**
- 临时使用：`export BJT_API_KEY=bjt_live_xxx`（首次调用自动落盘 config.json）。

## 完整流程

```bash
python3 scripts/bjt.py me                                          # 0. 自检（点数、限额）
python3 scripts/bjt.py upload 招标文件.pdf --type tender            # 1. 上传招标（自动等解析）
python3 scripts/bjt.py upload 投标文件.docx --type bid              # 2. 上传投标
python3 scripts/bjt.py review --tender <tid> --bid <bid_1> [--bid <bid_2>]   # 3. 审查（自动轮询）
# 查重：
python3 scripts/bjt.py upload A公司投标.docx --type duplicate_left
python3 scripts/bjt.py upload B公司投标.docx --type duplicate_right
python3 scripts/bjt.py duplicate <left_id> <right_id>              # 3'. 查重（自动轮询）
```

- 上传支持本地路径或 https URL（URL 会先下载到本地再上传）。
- **提交前必须完成计费确认**（见 billing.md 与 SKILL.md 铁律）：报告剩余点数 + 后扣费口径 + 失败也可能计费 + 数十分钟时长，用户明确同意后才执行第 3 步。
- `review`/`duplicate` 默认自动轮询至终态（超时 2h；Ctrl-C/会话中断不影响云端执行）。

## 断点续查 / 免提交场景

```bash
python3 scripts/bjt.py list                 # 找回最近任务 task_id
python3 scripts/bjt.py status <task_id>     # 单次查状态
python3 scripts/bjt.py result <task_id>     # 取结果 + 报告链接
python3 scripts/bjt.py cancel <task_id>     # 取消（可能仍计费，先告知用户）
```

会话中断、隔天回来、用户问「之前的检查好了吗」→ 一律先 `list` 再 `status`/`result`，**不要重新上传/重新提交**（会重复计费）。

## 边界情形

| 情形 | 处理 |
|---|---|
| 解析一直 `parsing` 超过 30 分钟 | `documents <id>` 再查；仍卡住让用户确认文件是否超大/扫描件，必要时换文件 |
| 解析 `failed` | 多为加密/损坏：投标文件常带加密，让用户解密导出后重传 |
| 409 `active_task_exists` | 每账号同时仅 1 个未结算任务：`list` 看进度，等终态后再提交下一个 |
| 查重 422 `identical_documents` | 两份文件内容完全相同：确认是否传错，换文件 |
| 402 `insufficient_balance` | 引导充值；**不要**在余额不足时反复重试提交 |
| 任务 `failed` | 把 error.code/message 总结给用户 + 说明费用口径；保留 task_id 供客服排查 |
| 用户催进度 | 如实转述当前 percent/stage_label；不要编造完成时间 |

## 环境要求

- Python 3.8+，无第三方依赖（WorkBuddy 自带 python 运行时；命令统一用 `python3`）。
- 需能访问 `check.aibjt.com:30002`（https）。
