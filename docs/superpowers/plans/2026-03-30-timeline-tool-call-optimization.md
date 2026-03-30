# 时间线工具调用节点优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化时间线工具调用节点，展示友好的工具名称、输入参数描述和结果摘要

**Architecture:** 后端 SSE 事件新增 `tool_args` 和 `tool_result` 字段；前端添加工具配置映射表，翻译工具名称、参数键值，提供结果格式化

**Tech Stack:** Python (FastAPI 后端), Vue3/TypeScript (前端)

---

## 文件映射

| 文件 | 职责 |
|------|------|
| `backend/agent/bid_review_agent.py` | SSE 事件发送时添加 tool_args 和 tool_result |
| `frontend/src/types/index.ts` | 扩展 SSEEvent 和 ToolResult 类型 |
| `frontend/src/components/ReviewTimeline.vue` | 工具映射配置、handleSSEEvent 提取字段、展示模板 |

---

## 任务 1: 后端 SSE 事件增强

**Files:**
- Modify: `backend/agent/bid_review_agent.py:156-190`

- [ ] **Step 1: 修改工具调用事件，添加 tool_args**

找到第 160-167 行的 `_send_event` 调用，修改为：

```python
# Send step event before tool execution
self._send_event("step", {
    "step_number": step_counter,
    "step_type": "tool_call",
    "tool_name": function_name,
    "content": f"Called {function_name}",
    "tool_args": tool_call.function.arguments,  # 新增
})
step_counter += 1
```

- [ ] **Step 2: 新增工具结果事件，携带 tool_result**

在第 171 行 `result = await self.tools[function_name].execute(...)` 之后添加结果事件发送：

```python
# Execute tool
if function_name in self.tools:
    result = await self.tools[function_name].execute(**tool_call.function.arguments)

    # 发送工具结果事件
    self._send_event("step", {
        "step_number": step_counter,
        "step_type": "tool_result",
        "tool_name": function_name,
        "content": f"{function_name} 返回",
        "tool_result": {
            "status": "success" if result.success else "error",
            "content": result.content if result.success else None,
            "error": result.error if not result.success else None,
            "count": getattr(result, 'count', None),
        },
    })
    step_counter += 1

    # Add tool message
    tool_msg = Message(
        role="tool",
        content=result.content if result.success else f"Error: {result.error}",
        tool_call_id=tool_call.id,
        name=function_name,
    )
    self.messages.append(tool_msg)
```

- [ ] **Step 3: 提交后端修改**

```bash
git add backend/agent/bid_review_agent.py
git commit -m "feat(agent): send tool_args and tool_result in SSE step events"
```

---

## 任务 2: 前端 SSEEvent 类型扩展

**Files:**
- Modify: `frontend/src/types/index.ts:110-121`

- [ ] **Step 1: 添加 ToolResult 接口**

在 `SSEEvent` 接口之前添加：

```typescript
export interface ToolResult {
  status: 'success' | 'error'
  content?: string
  error?: string
  count?: number
  data?: any
}
```

- [ ] **Step 2: 扩展 SSEEvent 接口**

在 SSEEvent 接口中添加新字段：

```typescript
export interface SSEEvent {
  type: 'status' | 'progress' | 'step' | 'complete' | 'error'
  task_id: string
  status?: string
  message?: string
  step_number?: number
  step_type?: string        // 新增 "tool_result"
  tool_name?: string
  content?: string
  findings_count?: number
  tool_args?: Record<string, any>   // 新增
  tool_result?: ToolResult           // 新增
}
```

- [ ] **Step 3: 提交类型修改**

```bash
git add frontend/src/types/index.ts
git commit -m "feat(types): add ToolResult interface and extend SSEEvent with tool_args/tool_result"
```

---

## 任务 3: 前端 ReviewTimeline 组件优化

**Files:**
- Modify: `frontend/src/components/ReviewTimeline.vue:20-32` (TimelineStep 接口)
- Modify: `frontend/src/components/ReviewTimeline.vue:53-69` (handleSSEEvent)
- Modify: `frontend/src/components/ReviewTimeline.vue:127-135` (getStepLabel)
- Modify: `frontend/src/components/ReviewTimeline.vue:196-207` (展示模板)

- [ ] **Step 1: 扩展 TimelineStep 接口**

将 TimelineStep 接口修改为：

```typescript
interface ToolResult {
  status: 'success' | 'error'
  content?: string
  error?: string
  count?: number
}

interface TimelineStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number
  tool_args?: Record<string, any>
  tool_result?: ToolResult
}
```

- [ ] **Step 2: 添加工具配置映射表**

在组件中的 `steps` ref 定义之后添加：

```typescript
// 工具名称映射
const toolNameMap: Record<string, string> = {
  search_tender_doc: '搜索文档',
  rag_search: '搜索知识库',
  comparator: '内容比对',
}

// 参数键映射 (每个工具独立)
const toolParamKeyMap: Record<string, Record<string, string>> = {
  search_tender_doc: {
    doc_type: '文档类型',
    query: '查询内容',
    chunk: '章节',
    full_content: '完整内容',
  },
  rag_search: {
    query: '查询内容',
    limit: '返回数量',
  },
  comparator: {
    requirement: '招标要求',
    bid_content: '投标内容',
  },
}

// 参数值映射 (每个工具独立)
const toolParamValueMap: Record<string, Record<string, Record<string, string>>> = {
  search_tender_doc: {
    doc_type: {
      tender: '招标文档',
      bid: '投标文档',
    },
  },
}

// 工具结果格式化
const toolResultFormatter: Record<string, (result: ToolResult) => string> = {
  search_tender_doc: (result) => {
    if (result.status === 'success') {
      return `找到 ${result.count || 0} 条相关内容 - ${result.content?.slice(0, 100)}...`
    }
    return `搜索失败: ${result.error}`
  },
  rag_search: (result) => {
    if (result.status === 'success') {
      return `知识库返回 ${result.count || 0} 条结果`
    }
    return `查询失败: ${result.error}`
  },
  comparator: (result) => {
    if (result.status === 'success') {
      return `比对完成: ${result.content?.slice(0, 100)}...`
    }
    return `比对失败: ${result.error}`
  },
}
```

- [ ] **Step 3: 修改 handleSSEEvent 提取 tool_args 和 tool_result**

将 handleSSEEvent 函数修改为：

```typescript
function handleSSEEvent(event: SSEEvent) {
  if (event.type === 'step' && event.step_number !== undefined) {
    const exists = steps.value.some(s => s.step_number === event.step_number)
    if (!exists) {
      steps.value.push({
        step_number: event.step_number,
        step_type: event.step_type || 'unknown',
        tool_name: event.tool_name,
        content: event.content || '',
        timestamp: new Date(),
        tool_args: event.tool_args,
        tool_result: event.tool_result,
      })
    }
  } else if (event.type === 'status' && event.status === 'running') {
    steps.value = []
  }
}
```

- [ ] **Step 4: 添加友好描述辅助函数**

在 `getStepLabel` 函数之后添加：

```typescript
function getFriendlyToolName(toolName?: string): string {
  if (!toolName) return '未知工具'
  return toolNameMap[toolName] || toolName
}

function getFriendlyArgs(toolName?: string, args?: Record<string, any>): Array<{key: string, value: string}> {
  if (!toolName || !args) return []
  const keyMap = toolParamKeyMap[toolName] || {}
  const valueMap = toolParamValueMap[toolName] || {}
  return Object.entries(args).map(([k, v]) => ({
    key: keyMap[k] || k,
    value: (valueMap[k] as Record<string, string>)?.[String(v)] || String(v),
  }))
}

function getFriendlyResult(toolName?: string, result?: ToolResult): string {
  if (!toolName || !result) return ''
  const formatter = toolResultFormatter[toolName]
  if (formatter) {
    return formatter(result)
  }
  return result.status === 'success' ? `完成: ${result.content?.slice(0, 50)}...` : `失败: ${result.error}`
}
```

- [ ] **Step 5: 修改 getStepLabel 函数**

将 `getStepLabel` 函数修改为使用友好名称：

```typescript
function getStepLabel(stepType: string, toolName?: string): string {
  if (stepType === 'tool_call') {
    return `工具调用: ${getFriendlyToolName(toolName)}`
  }
  if (stepType === 'tool_result') {
    return `工具返回: ${getFriendlyToolName(toolName)}`
  }
  if (stepType === 'observation') {
    return '观察'
  }
  return '思考过程'
}
```

- [ ] **Step 6: 修改模板中的工具调用展示**

将第 196-207 行的折叠面板替换为：

```vue
<!-- 可折叠详细信息（仅工具调用和工具返回有此项） -->
<Collapse v-if="(step.step_type === 'tool_call' || step.step_type === 'tool_result') && (step.tool_args || step.tool_result)" class="tool-collapse" ghost>
  <CollapsePanel key="1" header="显示详细信息">
    <!-- 工具调用参数 -->
    <template v-if="step.step_type === 'tool_call' && step.tool_args">
      <div class="tool-section">
        <strong>调用参数:</strong>
        <div class="params-list">
          <div v-for="param in getFriendlyArgs(step.tool_name, step.tool_args)" :key="param.key" class="param-item">
            <span class="param-key">{{ param.key }}:</span>
            <span class="param-value">{{ param.value }}</span>
          </div>
        </div>
      </div>
    </template>

    <!-- 工具返回结果 -->
    <template v-if="step.step_type === 'tool_result' && step.tool_result">
      <div class="tool-section">
        <strong>返回结果:</strong>
        <div class="result-text">{{ getFriendlyResult(step.tool_name, step.tool_result) }}</div>
      </div>
    </template>
  </CollapsePanel>
</Collapse>
```

- [ ] **Step 7: 添加新增的样式**

在 `tool-result` 样式之后添加：

```css
.tool-section {
  margin-bottom: 0.75rem;
}

.tool-section strong {
  display: block;
  margin-bottom: 0.25rem;
  font-size: 0.85rem;
  color: #666;
}

.params-list {
  padding: 8px;
  background: rgb(245, 245, 245);
  border-radius: 4px;
}

.param-item {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.25rem;
  font-size: 0.85rem;
}

.param-key {
  color: #1890ff;
  font-weight: 500;
}

.param-value {
  color: #333;
}

.result-text {
  padding: 8px;
  background: rgb(245, 245, 245);
  border-radius: 4px;
  font-size: 0.85rem;
  color: #52c41a;
  white-space: pre-wrap;
}
```

- [ ] **Step 8: 提交前端修改**

```bash
git add frontend/src/types/index.ts frontend/src/components/ReviewTimeline.vue
git commit -m "feat(timeline): display friendly tool name, args and result summary"
```

---

## 自检清单

- [ ] 设计文档中的每个功能点都有对应的任务步骤
- [ ] 所有代码示例中的路径、函数名、变量名都与上下文一致
- [ ] 没有 placeholder (TBD, TODO, "add appropriate error handling" 等)
- [ ] 类型一致性检查：TimelineStep.tool_result 类型与 SSEEvent.tool_result 一致
- [ ] getStepLabel 函数处理了 tool_result 类型
