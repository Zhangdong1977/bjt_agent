# 批量合并与重试机制设计

## 背景

当前合并实现存在以下问题：
1. **逐条调用LLM** - 每个finding单独调用一次LLM，效率低
2. **无重试机制** - LLM调用失败时直接使用fallback策略

## 目标

1. 任务内子代理结果**批量合并**，只调用一次LLM
2. 项目历史结果**批量合并**，只调用一次LLM
3. LLM调用添加**固定次数重试机制**

---

## 设计

### 1. 批量合并Prompt设计

#### 任务内子代理批量合并（TaskMergeService）

**Prompt输入**：
```
你是专业的标书审查结果合并决策专家，负责将多个审查发现与现有发现进行批量合并。

## 决策原则

每次审查都应该被保留，除非新发现与某历史发现**完全重复**（实质内容相同）。

- keep - 保留新发现作为独立条目
- replace - 用新发现替换某个现有发现（需指定replace_key）
- discard - 丢弃新发现（只有当新发现与某现有发现实质内容完全相同时）

## 新发现列表：
{new_findings_json}

## 现有发现列表：
{existing_findings_json}

## 输出要求

请对**每个新发现**给出决策，格式如下：

1. 新发现[序号] (requirement_key: xxx)
   决策：keep | replace | discard
   理由：[详细解释为什么做出这个决策，30-100字]
   替换key：[如果决策是replace，填入被替换的requirement_key，否则填"无"]
```

#### 项目历史结果批量合并（MergeService）

使用**相同的Prompt模板**，因为语义相同：都是"将新发现列表与现有发现列表进行批量合并"。

---

### 2. 批量决策解析

返回格式是逐条自然语言，需要解析每个finding的决策。

解析逻辑：
1. 按"新发现[序号]"或序号规则分割响应
2. 每条提取：requirement_key、决策、理由、替换key
3. 如果解析失败，该条使用 `keep_both` fallback

---

### 3. 重试机制

在 `LLMClient` 调用层添加装饰器/封装：

```python
async def call_with_retry(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = await llm_client.generate(messages=[...])
            return response
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"LLM call failed (attempt {attempt+1}/{max_retries}): {e}")
            await asyncio.sleep(1)  # 简单等待后重试
```

---

### 4. 流程变更

#### TaskMergeService.merge_sub_agent_results

**Before**（逐条）：
```
findings: [A, B, C]
for each finding:
    call LLM once → decision
    apply decision
```

**After**（批量）：
```
findings: [A, B, C]
batch_call_LLM([A, B, C], existing=[]) → decisions
apply all decisions
```

#### MergeService.merge_project_results

**Before**（逐条）：
```
latest_results: [A, B, C]
for each result:
    call LLM once → decision
    apply decision
```

**After**（批量）：
```
latest_results: [A, B, C]
batch_call_LLM([A, B, C], existing=existing_merged) → decisions
apply all decisions
```

---

## 关键变更文件

1. `backend/services/task_merge_service.py` - 批量合并逻辑
2. `backend/services/merge_service.py` - 批量合并逻辑
3. `backend/services/merge_decision_parser.py` - 支持批量解析
4. `backend/agent/tools/merge_decider.py` - 添加重试机制

---

## 测试策略

1. **单元测试**：批量解析正确性
2. **集成测试**：批量合并结果与逐条合并结果一致
3. **重试测试**：模拟LLM失败场景验证重试行为
