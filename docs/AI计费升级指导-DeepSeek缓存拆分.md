# AI 计费升级指导 · DeepSeek 缓存拆分按官方费率计价

> **升级目标**：让 `deepseek-v4-flash` 的每次调用按 DeepSeek **中国官方人民币费率**精确计费（区分缓存命中/未命中输入），并把成本数据准确同步到运营管理平台（operate-two）。
>
> **适用版本**：bjt-agent（master 分支）+ operate-two（operate-two 分支），本次升级的提交之后。
> **编写日期**：2026-06-20
> **费率来源**：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/

---

## 一、本次升级做了什么

### 背景
原有的 AI 用量计费链路（`ai_usage_records` 落库 → `cost_calculator` 估算 → `/api/admin/usage/records` 增量同步 → operate-two 镜像表）**已经存在**，但对 `deepseek-v4-flash` 计费不准：

1. **丢弃了缓存拆分**：DeepSeek 在 `usage` 里返回 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，但 Mini-Agent 的 `TokenUsage` schema 没有这两个字段，解析时直接丢弃，导致全部输入按"未命中"单价计算，**虚高约 50 倍**。
2. **价目表缺 v4-flash**：`cost_calculator` 没有 `deepseek-v4-flash` 条目，落到 `__default__`（旧 chat 价 2元/8元），与官方费率不符。
3. **同步链路有潜伏 bug**：operate-two 的 `toEntity` 用 `getInteger("interior_user")` 解析布尔值，fastjson2 会抛 `Can not cast Boolean to Integer`，导致用户有 `interior_user` 值的记录全部静默同步失败。

### 修复内容

#### bjt-agent 侧（含 Mini-Agent submodule）
| 文件 | 改动 |
|------|------|
| `Mini-Agent/mini_agent/schema/schema.py` | `TokenUsage` 增加 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 两字段 |
| `Mini-Agent/mini_agent/llm/openai_client.py` | `_parse_response` 用 `getattr` 填充 cache 字段（非 deepseek provider 兜底为 0） |
| `backend/models/ai_usage_record.py` | `AiUsageRecord` ORM 增加 2 列 |
| `backend/services/cost_calculator.py` | **核心**：v4-flash 三档费率（命中0.02/未命中1/输出2 元每百万token）+ `_llm_cost` 按档计价 + 无拆分时兜底 |
| `backend/services/usage_recorder.py` | 取 cache 字段并传给 `estimate_cost` + 落库 |
| `backend/api/admin.py` | `_serialize` 输出 cache 字段供 operate-two 拉取 |
| `backend/agent/bid_review_agent.py` | 交互日志的 usage dict 补 cache 字段 |
| `backend/celery_app.py` | 顶部 `sys.stdout/stderr.reconfigure(utf-8)` —— 修复 Windows celery worker 下 Mini-Agent emoji print 的 GBK 崩溃 |
| `scripts/bjt.bat` | 设置 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` |
| `backend/migrations/012_add_cache_tokens_to_ai_usage_records.sql`（新增） | PG 表幂等 ALTER 加 2 列 |
| `backend/tests/test_ai_usage_review.py` | 增加 v4-flash 三档费率断言 + 结构断言更新 |

#### operate-two 侧
| 文件 | 改动 |
|------|------|
| `sql/ai_usage_deploy.sql` | DDL 加 2 列 + 末尾幂等 ALTER（INFORMATION_SCHEMA + PREPARE，可重复执行） |
| `document-web/.../domain/AiUsage.java` | 实体加 `promptCacheHitTokens` / `promptCacheMissTokens`（带 `@Excel` 注解） |
| `document-web/.../mapper/document/AiUsageMapper.xml` | resultMap / upsert / select / overview / dailyTrend 全补，聚合新增 cache 命中率维度 |
| `document-web/.../service/impl/AiUsageServiceImpl.java` | **toEntity 修复** `interior_user` 类型转换（fastjson2 布尔值崩溃）+ 移除方法级 `@Transactional`（逐条 upsert 各自原子提交） |

---

## 二、官方费率口径（生产环境务必核对最新页面）

`deepseek-v4-flash`（中国官网，人民币 / 每百万 token）：

| 计费项 | 单价 | 对应字段 |
|--------|------|----------|
| 缓存命中输入 | **0.02 元** | `prompt_cache_hit_tokens` |
| 缓存未命中输入 | **1.0 元** | `prompt_cache_miss_tokens` |
| 输出 | **2.0 元** | `completion_tokens` |

**计费公式**（`cost_calculator._llm_cost`）：
```
cost_cny = hit_tokens × 0.02/1M + miss_tokens × 1.0/1M + completion_tokens × 2.0/1M
```

> ⚠️ DeepSeek 保留调价权利。调价时**只需改 `backend/services/cost_calculator.py` 的 `_DEEPSEEK` 价目表**（唯一来源），运营台只原值透传 `cost_cny`，不做二次计算，避免两处价目漂移。

> 💡 标书审查场景（长 system prompt + 同一招标文件多轮提问）缓存命中率很高，实测本次环境约 **91%**，命中部分是未命中单价的 **1/50**，计费精确后成本会显著下降。

---

## 三、生产环境升级步骤

### 前置确认
- [ ] 备份 bjt-agent 的 PostgreSQL 数据库（`bjt_agent` 库）
- [ ] 备份 operate-two 的 MySQL 数据库
- [ ] 确认 DeepSeek 官方费率未调整（见第二节链接）
- [ ] 准备**短暂停机窗口**（bjt-agent + operate-two 都需重启，约 5~10 分钟）

### 步骤 1：拉取代码并部署

**bjt-agent**（含 Mini-Agent submodule）：
```bash
cd <bjt-agent 部署目录>
git pull origin master
git submodule update --init --recursive   # 确保 Mini-Agent 指针同步
# 如果 pip 依赖无变化，无需重新安装
```

**operate-two**：
```bash
cd <operate-two 部署目录>
git pull origin operate-two
mvn clean package -DskipTests              # 用 JDK 8 编译打包
```

### 步骤 2：执行数据库迁移（DDL）

**⚠️ 两个 DDL 都必须执行，否则新记录写入/读取会报"列不存在"。两个脚本均幂等，可重复执行。**

**bjt-agent 的 PostgreSQL**（给 `ai_usage_records` 表加 2 列）：
```bash
psql "<生产 PG 连接串>/bjt_agent" \
  -f backend/migrations/012_add_cache_tokens_to_ai_usage_records.sql
```
> 全新库由 `init_db()` 的 `create_all` 自动建列；已存在的库需手动跑此脚本。
> 执行后历史数据保持不变（新列默认 0）。

**operate-two 的 MySQL**（给 `ai_usage_record` 镜像表加 2 列）：
```bash
mysql -h<host> -u<user> -p<生产库> < sql/ai_usage_deploy.sql
```
> 脚本幂等：菜单/定时任务/游标已存在则跳过；两列用 `INFORMATION_SCHEMA` 判断后 `ALTER ADD`。

### 步骤 3：重启服务

按依赖顺序重启：

1. **bjt-agent backend**（uvicorn）+ **Celery worker**
   - ⚠️ Celery worker **必须重启**，否则新代码（cache 解析、UTF-8 修复）不生效
   - 确保启动环境有 `PYTHONIOENCODING=utf-8`（Windows 下尤其重要，避免 emoji print 崩溃）

2. **operate-two**（Spring Boot）
   - 重启后 Quartz 会自动按 `0 0/3 * * * ?`（每 3 分钟）增量同步

3. 前端无需改动（本次未涉及前端代码）

### 步骤 4：验证（升级后自检）

**触发一次真实标书审查任务**，然后按下面三个检查点验证：

#### 检查点 A：bjt-agent 计费正确
查 PostgreSQL，确认新记录的 cache 字段非零、cost 按官方费率：
```sql
SELECT created_at, prompt_tokens, prompt_cache_hit_tokens, prompt_cache_miss_tokens,
       completion_tokens, cost_cny
FROM ai_usage_records
WHERE usage_type='llm' AND created_at > '<升级时间>'
ORDER BY created_at DESC LIMIT 5;
```
预期：`prompt_cache_hit_tokens > 0`（命中率高的场景），且 `cost_cny` 显著低于升级前（因为命中部分 50 倍便宜）。

#### 检查点 B：增量同步正常
查 operate-two MySQL，确认镜像表数量与 bjt-agent 一致、cache 字段已同步：
```sql
-- operate-two 库
SELECT COUNT(*) AS total,
       SUM(CASE WHEN prompt_cache_hit_tokens>0 OR prompt_cache_miss_tokens>0 THEN 1 ELSE 0 END) AS with_cache,
       MAX(source_created_at) AS latest
FROM ai_usage_record;
```
预期：`total` 与 bjt-agent 的 `ai_usage_records` 行数一致；`with_cache > 0`；`latest` 为近期时间。

#### 检查点 C：运营看板接口
调用 operate-two 接口确认聚合正常：
```
GET http://<operate-two>/ai/usage/overview
```
预期：返回含 `cache_hit_tokens` / `cache_miss_tokens` 字段，可计算缓存命中率。

---

## 四、回滚方案

若升级后出现问题，按相反顺序回滚：

1. **代码回滚**：
   ```bash
   # bjt-agent
   git checkout <升级前 commit> -- .
   git submodule update --init   # Mini-Agent 指针回退
   # operate-two
   git checkout <升级前 commit> -- .
   ```
2. **数据库**：新增的 2 列**无需删除**（向后兼容，默认 0 不影响旧代码运行）。如确需清理：
   ```sql
   -- PG (bjt-agent)
   ALTER TABLE ai_usage_records DROP COLUMN IF EXISTS prompt_cache_hit_tokens;
   ALTER TABLE ai_usage_records DROP COLUMN IF EXISTS prompt_cache_miss_tokens;
   -- MySQL (operate-two)
   ALTER TABLE ai_usage_record DROP COLUMN prompt_cache_hit_tokens;
   ALTER TABLE ai_usage_record DROP COLUMN prompt_cache_miss_tokens;
   ```
3. 重启两个服务。

---

## 五、历史数据处理说明

- **本次升级只影响新记录**，历史 `ai_usage_records` 行的 `cost_cny` 保持原值（升级前的粗估值），新增的 cache 列为 0。
- **历史 raw_usage JSON 里其实已含 cache 拆分**（openai SDK 一直保留了 extra 字段，只是没被结构化提取）。如需把历史成本也修正为精确值，可另写一次性脚本从 `raw_usage` 反解 cache 并用新费率重算 `cost_cny` 回灌——本次升级**不包含**此操作，按需单独执行。

---

## 六、运维注意事项

1. **调价后整体重算**：DeepSeek 调价时，改 `cost_calculator.py` 的 `_DEEPSEEK` 价目表并重启即可，新记录按新价计算。历史记录如需重算见第五节。
2. **缓存命中率异常下跌排查**：DeepSeek 的缓存是"整段前缀完全匹配"，且有时效（几小时到几天）。若发现命中率骤降，检查：① system prompt + 招标文件前缀是否被改动（含标点空白）② 是否长时间未调用导致缓存过期。
3. **多 provider 切换**：`cost_calculator` 对 minimax/volcengine 也保留了 `hit=0, miss=input` 的兜底（它们无缓存拆分），切换 provider 不影响计费逻辑。
4. **日志过滤坑**（operate-two）：logback 的 `sys-info.log` 用 `LevelFilter INFO + onMismatch=DENY`，**WARN 级别日志会被完全丢弃**（不进 info 也不进 error 文件）。排查同步类问题时，相关日志级别需设为 ERROR 或直接看 console。
5. **Windows 部署的编码**：celery worker 若在中文 Windows 的 cmd 窗口运行，务必确保 `PYTHONIOENCODING=utf-8`（本次已在 `bjt.bat` 和 `celery_app.py` 双重设置），否则 Mini-Agent 的 emoji print 会导致审查任务崩溃。

---

## 七、本次实测数据（参考）

升级后在测试环境跑一次完整审查任务的实测结果：

| 维度 | 数据 |
|------|------|
| 审查任务状态 | completed，耗时 122s |
| LLM 调用次数 | 80 次（success） |
| 缓存命中率 | **91.1%**（命中 260096 token / 未命中 25379 token） |
| 总成本（精确） | **1.2579 元** |
| 同步到 operate-two | 80 条全部成功，cache 字段正确填充 |

**对比升级前**：以单条 prompt=15997(hit=15872,miss=125) 为例——
- 升级前（全部按 miss 价 2元/M 粗估）：`15997 × 2/1M = 0.0320 元`
- 升级后（精确分档）：`15872×0.02/M + 125×1/M + 190×2/M = 0.000822 元`
- **精确值是粗估的 1/39**，充分体现了缓存拆分计费的价值。
