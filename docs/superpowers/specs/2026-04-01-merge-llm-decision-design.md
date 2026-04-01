# 标书审查结果合并 - LLM 决策设计方案

## 概述

将合并逻辑从 **embedding 相似度判断** 改为 **LLM 决策判断**，复用现有 `BidReviewAgent` 的 LLM client，实现更智能的合并去重。

## 背景问题

当前合并方案使用 embedding 相似度（0.85阈值）判断两个发现是否重复，存在以下问题：

1. **requirement_key 不稳定**：每轮任务独立生成 `req_001, req_002...`，导致相同内容的发现可能被误判为不同
2. **相似度阈值敏感**：0.85 阈值在某些场景下过高或过低
3. **语义理解不足**：embedding 只能判断文本相似，无法理解业务语义

## 设计目标

1. 使用 LLM 理解业务语义，做出更准确的合并决策
2. 复用现有 BidReviewAgent 的 LLM client，保持架构简洁
3. 保持异步任务架构不变
4. 解析失败时采用保守策略，避免丢失信息

## 架构设计

### 组件关系

```
run_review (Celery Task)
    │
    ▼
merge_review_results (Celery Task)
    │
    ▼
MergeService.merge_project_results()
    │
    ├──► BidReviewAgent (with MergeDeciderTool)
    │         │
    │         ▼
    │    LLM 决策 (自然语言输出)
    │
    ▼
解析决策结果 → 执行合并 → 存储到 project_review_results
```

### 核心组件

#### 1. MergeDeciderTool

新增工具类，供 BidReviewAgent 调用 LLM 进行合并决策。

**文件位置**：`backend/agent/tools/merge_decider.py`

**类定义**：

```python
class MergeDeciderTool:
    """Tool for LLM to decide merge strategy between findings."""

    name: str = "decide_merge"
    description: str = "决定新发现与历史发现的合并策略"

    async def execute(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> dict:
        """
        Args:
            new_finding: 新发现的完整信息
            existing_findings: 现有发现列表

        Returns:
            LLM 自然语言决策结果
        """
```

#### 2. 提示词设计

系统提示词中新增 MergeDecider 角色：

```
## 合并决策专家模式

当你收到 merge_decide 工具调用时，你需要分析：

### 输入信息
- 1个新发现（来自最新审查任务）
- N个现有发现（来自历史合并结果）

### 决策选项
1. keep - 保留新发现（与现有所有发现都不重复或新发现信息更丰富）
2. replace - 用新发现替换某个现有发现（新发现更完整/severity更高/位置更精确）
3. discard - 丢弃新发现（与某现有发现重复且没有提供任何新信息）

### 输出格式（自然语言，必须包含）：
决策：keep | replace | discard
理由：[详细解释为什么做出这个决策，50-200字]
替换key：[如果决策是replace，填入被替换的 requirement_key，否则填"无"]
```

#### 3. 决策解析器

**文件位置**：`backend/services/merge_decision_parser.py`

```python
def parse_merge_decision(text: str) -> dict:
    """解析 LLM 输出的自然语言决策。

    Args:
        text: LLM 输出的自然语言决策

    Returns:
        {
            "action": "keep" | "replace" | "discard" | "keep_both",
            "reason": str,
            "replace_key": str | None,
            "parse_failed": bool
        }

    解析失败时返回：
        {
            "action": "keep_both",
            "reason": "解析失败，保留新旧两条记录",
            "replace_key": None,
            "parse_failed": True
        }
    """
```

#### 4. MergeService 修改

**文件位置**：`backend/services/merge_service.py`

**修改点**：

1. 构造函数增加 `agent: BidReviewAgent` 参数
2. 新增方法 `async _get_llm_merge_decision()`
3. 修改 `merge_project_results()` 使用 LLM 决策

```python
class MergeService:
    def __init__(self, db: AsyncSession, agent: BidReviewAgent):
        self.db = db
        self.agent = agent

    async def _get_llm_merge_decision(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> dict:
        """调用 LLM 获取合并决策。"""
        result = await self.agent.decide_merge(new_finding, existing_findings)
        return parse_merge_decision(result)

    async def merge_project_results(self, ...):
        # ... 获取 historical_results 和 existing_merged ...

        for new_result in latest_results:
            req_key = new_result.get("requirement_key", "")

            if req_key in existing_by_key:
                existing = existing_by_key[req_key]

                # 使用 LLM 决策
                decision = await self._get_llm_merge_decision(
                    new_result,
                    [existing]
                )

                if decision["action"] == "keep":
                    # 保留新发现，标记与旧发现合并
                    new_result["merged_from_count"] = 2
                    new_merged_records.append(new_result)
                    matched_keys.add(req_key)
                elif decision["action"] == "replace":
                    # 用新发现替换
                    existing.update(new_result)
                    existing["merged_from_count"] = existing.get("merged_from_count", 1) + 1
                    new_merged_records.append(existing)
                    matched_keys.add(req_key)
                elif decision["action"] == "discard":
                    # 丢弃新发现，保留现有
                    new_merged_records.append(existing)
                    matched_keys.add(req_key)
                elif decision["action"] == "keep_both":
                    # 解析失败，保留两条
                    new_result["merged_from_count"] = 1
                    existing["merged_from_count"] = existing.get("merged_from_count", 1)
                    new_merged_records.append(existing)
                    new_merged_records.append(new_result)
                    matched_keys.add(req_key)
            else:
                # 新 key，直接添加
                new_result["merged_from_count"] = 1
                new_merged_records.append(new_result)
                matched_keys.add(req_key)

        # ... 后续处理不变 ...
```

#### 5. BidReviewAgent 扩展

**文件位置**：`backend/agent/bid_review_agent.py`

**修改点**：

1. `__init__` 中注册 `MergeDeciderTool`
2. 新增 `decide_merge()` 方法

```python
class BidReviewAgent(BaseAgent):
    def __init__(self, ...):
        # ... 现有初始化代码 ...

        # 新增 MergeDeciderTool
        tools = [
            DocSearchTool(...),
            RAGSearchTool(...),
            ComparatorTool(),
            MergeDeciderTool(),  # 新增
        ]

        # ... 后续不变 ...

    async def decide_merge(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> str:
        """调用 LLM 进行合并决策。"""
        # 构建提示词
        prompt = self._build_merge_decision_prompt(new_finding, existing_findings)

        # 调用 LLM
        response = await self.llm_client.chat([
            Message(role="user", content=prompt)
        ])

        return response.content
```

## 数据流

### 新发现合并流程

```
1. run_review 完成，生成新 ReviewResult (req_001 ~ req_005)
              │
              ▼
2. merge_review_results Celery 任务触发
              │
              ▼
3. MergeService 获取：
   - latest_results: [req_001, req_002, req_003, req_004, req_005]
   - existing_merged: [req_001, req_002, req_003, req_004, req_005, req_006, req_007]
              │
              ▼
4. 对每条新结果，调用 LLM 决策：
   LLM: "决策：replace，理由：新发现的位置信息更精确..."
              │
              ▼
5. 根据决策执行合并
              │
              ▼
6. 删除旧 project_review_results，插入新记录
```

## 错误处理

### LLM 调用失败

```python
async def _get_llm_merge_decision(self, new_finding: dict, existing_findings: list[dict]) -> dict:
    try:
        decision = await self.agent.decide_merge(new_finding, existing_findings)
        return parse_merge_decision(decision)
    except Exception as e:
        logger.warning(f"LLM merge decision failed: {e}, using keep_both strategy")
        return {
            "action": "keep_both",
            "reason": f"LLM调用失败: {str(e)}",
            "replace_key": None,
            "parse_failed": True
        }
```

### 解析失败

```python
def parse_merge_decision(text: str) -> dict:
    try:
        # 解析逻辑
        decision = extract_decision(text)
        # ...
        return result
    except Exception as e:
        logger.warning(f"Failed to parse merge decision: {e}, text: {text}")
        return {
            "action": "keep_both",
            "reason": f"解析失败: {str(e)}",
            "replace_key": None,
            "parse_failed": True
        }
```

## 测试计划

### 单元测试

1. **parse_merge_decision 测试**
   - 正常解析 keep/replace/discard
   - 解析失败回退到 keep_both
   - 各种边界情况

2. **MergeDeciderTool.execute 测试**
   - 模拟 LLM 响应
   - 验证参数传递正确

### 集成测试

1. **完整合并流程测试**
   - 模拟两轮审查结果
   - 验证合并结果正确

2. **边界情况测试**
   - 空 existing_findings
   - LLM 调用超时
   - 解析失败回退

## 文件变更清单

| 文件 | 操作 | 描述 |
|------|------|------|
| `backend/agent/tools/merge_decider.py` | 新增 | MergeDeciderTool 实现 |
| `backend/agent/bid_review_agent.py` | 修改 | 注册新工具，新增 decide_merge 方法 |
| `backend/agent/prompt.py` | 修改 | 增加合并决策提示词 |
| `backend/services/merge_service.py` | 修改 | 使用 LLM 决策替代 embedding |
| `backend/services/merge_decision_parser.py` | 新增 | LLM 输出解析器 |
| `tests/unit/test_merge_decision_parser.py` | 新增 | 解析器单元测试 |
| `tests/integration/test_merge_service.py` | 新增 | 合并服务集成测试 |

## 实施顺序

1. 实现 `merge_decision_parser.py` - 解析器（无依赖）
2. 实现 `merge_decider.py` - 工具类（无依赖）
3. 修改 `prompt.py` - 增加提示词
4. 修改 `bid_review_agent.py` - 注册工具和决策方法
5. 修改 `merge_service.py` - 使用 LLM 决策
6. 编写单元测试
7. 编写集成测试
8. 手动验证
