# bjt-agent 文档索引

> bjt-agent（AI 标书审查智能体）项目文档。本目录入库 GitHub，**只写 IP/架构，不写密码**。

## 两层文档分工（重要）

本项目文档分两层，互补不重复：

| 层 | 目录 | 入库 | 定位 |
|---|---|---|---|
| **设计手册** | `bjt-agent/docs/`（本目录）| ✅ GitHub | 项目设计、架构、部署运维手册（**只写 IP 不写密码**）|
| **凭证事实簿** | [`../../doc/workspace/`](../../doc/workspace/)（sales 顶层）| ❌ 非 git | 服务器密码/密钥/token、跨项目（operate-two↔bjt-agent）事实、AI 用量同步链路 |

- **凭证**（SSH 密码、DB 密码、GitHub token、USAGE_SYNC_API_KEY）只在 [`05-production-env.md`](../../doc/workspace/05-production-env.md)，本目录不写。
- **集群落地实况唯一权威源 = [集群部署方案.md](集群部署方案.md) §3.9**；day-2 运维 = [集群维护速查手册.md](集群维护速查手册.md)。

## 文档定位表

### 部署与运维（最高频）
| 文档 | 看什么 |
|---|---|
| [集群部署方案.md](集群部署方案.md) | 集群设计 + **§3.9 落地实况（权威源）**：4 机拓扑、角色、NFS/PgBouncer/Redis、Phase 状态 |
| [集群维护速查手册.md](集群维护速查手册.md) | 集群 day-2 运维 runbook：巡检、起停、代码更新、扩缩容、故障、回滚 |
| [运行操作指导.md](运行操作指导.md) | 单机（§1）+ 集群（§2，通用模板）运行操作 |

### 架构与需求
| 文档 | 看什么 |
|---|---|
| [项目需求.md](项目需求.md) | 需求规格 + 系统架构（§2 含架构图、组件职责、**核心数据流、6 张时序图**）|
| [mini-agent-interaction.md](mini-agent-interaction.md) | BidReviewAgent 与 Mini-Agent 子模块交互（工具、消息 schema、Agent 循环、SSE）|
| [标书查重功能设计与开发计划.md](标书查重功能设计与开发计划.md) | 标书查重 V1 需求、双模式架构、数据/API、Agent、计费、测试和实施状态 |

### 数据
| 文档 | 看什么 |
|---|---|
| [database_schema.md](database_schema.md) | PG 表结构、ER 图、索引（⚠️ 缺 `ai_usage_records`/`ai_usage_task_summary`，见 05 §三）|

### AI / 经验学习
| 文档 | 看什么 |
|---|---|
| [审查经验自学习方案.md](审查经验自学习方案.md) | 经验自学习方案设计（六大机制、schema、prompt、路线）|
| [经验消化管道开发计划.md](经验消化管道开发计划.md) | 上述方案的工程实施计划（Phase A–D）|
| [标书审核任务异常保护与反馈机制改进计划.md](标书审核任务异常保护与反馈机制改进计划.md) | 审查链路异常保护改进 |
| [智谱API使用指南.md](智谱API使用指南.md) | 智谱 Embedding 接口配置 |
| [ocr_comparison_report.md](ocr_comparison_report.md) | 百度 OCR vs MiniMax 图像理解对比 |

### 子模块 / 其他
| 文档 | 看什么 |
|---|---|
| [子模块管理手册.md](子模块管理手册.md) | Mini-Agent git 子模块克隆/同步/修改 |
| [开发日报-2026-07-03.md](开发日报-2026-07-03.md) | 单日开发日报 |

## 子目录
- [`rules-all/`](rules-all/) — 完整审查规则集（A001–E001 等 16 条）
- [`rules-duplicate/`](rules-duplicate/) — 标书查重规则（YAML Front Matter + Agent 指令）
- [`protocal/`](protocal/) — 法律文本（用户协议、隐私政策）
- [`ui/`](ui/) — UI 设计稿切图 + [切图清单.md](ui/切图清单.md)

## 已删除的过时文档（2026-07-06）
以下设计期文档已删除（内容已被上方现行文档覆盖，或与生产现实脱节）：
- `部署手册.md`（docker-compose/MiniMax/`:3000` 单机）→ 用 [运行操作指导.md](运行操作指导.md) + [集群部署方案.md](集群部署方案.md)
- `架构说明.md`（端口 `7004/7005`、MiniMax）→ 时序图/数据流已并入 [项目需求.md](项目需求.md) §2.3/§2.4
- `系统上线前准备清单.md`、`AI计费升级指导-DeepSeek缓存拆分.md`（同步链路描述与 05 §四 任务级链路冲突）

---

> **维护约定**：新增文档请在本表登记一行；凭证类信息勿写本目录，放 `../../doc/workspace/05-production-env.md`。
