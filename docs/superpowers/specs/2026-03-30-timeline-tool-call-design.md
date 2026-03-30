# 时间线工具调用节点优化设计方案

## 1. 背景与目标

当前时间线中，工具调用节点只显示 "Called search_tender_doc"，缺少对调用工具的输入参数和输出结果的友好描述。本方案旨在优化时间线展示，提供更易读的友好描述。

## 2. 数据流架构

```
BidReviewAgent (后端)
    │
    ├── 执行工具 → 收集 tool_args 和 tool_result
    │
    ├── 发送 SSE 事件 (新增 tool_args, tool_result 字段)
    │
    ▼
SSE Event
    │
    ├── step_number, step_type, tool_name
    ├── content: "Called search_tender_doc"
    ├── tool_args: { doc_type: "tender", query: "技术规格", chunk: 1 }
    └── tool_result: { status: "success", content: "...", count: 3 }
    │
    ▼
Frontend SSE 接收
    │
    ├── 提取 tool_args, tool_result 到 TimelineStep
    │
    ▼
ReviewTimeline.vue
    │
    ├── 查找工具映射配置 (toolConfigMap)
    ├── 翻译工具名称: search_tender_doc → 搜索文档
    ├── 翻译参数键: doc_type → 文档类型
    ├── 翻译参数值: tender → 招标文档
    └── 格式化结果摘要
```

## 3. 后端修改

### 3.1 文件
- `backend/agent/bid_review_agent.py`

### 3.2 修改内容

1. 工具调用事件添加 `tool_args` 字段
2. 新增工具结果事件 `tool_result`，携带执行结果

```python
# Execute tools
for tool_call in response.tool_calls:
    function_name = tool_call.function.name

    # Send step event before tool execution
    self._send_event("step", {
        "step_number": step_counter,
        "step_type": "tool_call",
        "tool_name": function_name,
        "content": f"Called {function_name}",
        "tool_args": tool_call.function.arguments,  # 新增
    })
    step_counter += 1

    # Execute tool
    if function_name in self.tools:
        result = await self.tools[function_name].execute(**tool_call.function.arguments)

        # 发送工具结果事件
        self._send_event("step", {
            "step_number": step_counter,
            "step_type": "tool_result",
            "tool_name": function_name,
            "content": f"{function_name} 返回",
            "tool_result": result,  # 新增
        })
        step_counter += 1
```

## 4. 前端修改

### 4.1 文件
- `frontend/src/types/index.ts`
- `frontend/src/components/ReviewTimeline.vue`

### 4.2 SSEEvent 类型扩展

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

export interface ToolResult {
  status: 'success' | 'error'
  content?: string
  count?: number
  data?: any
}
```

### 4.3 工具配置映射表

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
    return `搜索失败: ${result.content}`
  },
  rag_search: (result) => {
    if (result.status === 'success') {
      return `知识库返回 ${result.count || 0} 条结果`
    }
    return `查询失败: ${result.content}`
  },
  comparator: (result) => {
    if (result.status === 'success') {
      return `比对完成: ${result.content}`
    }
    return `比对失败: ${result.content}`
  },
}
```

### 4.4 TimelineStep 接口扩展

```typescript
interface TimelineStep {
  step_number: number
  step_type: 'thought' | 'tool_call' | 'observation' | 'tool_result'
  tool_name?: string
  content: string
  timestamp: Date
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number
  tool_args?: Record<string, any>      // 新增
  tool_result?: ToolResult             // 新增
}
```

## 5. 展示效果

### 当前状态
```
第 2 节
🔧 工具调用: search_tender_doc
22:38:06
Called search_tender_doc
```

### 优化后
```
第 2 节
🔧 搜索文档
22:38:06
调用参数:
   文档类型: 招标文档
   查询内容: 技术规格要求
   章节: 1
──────────────────────────────────
返回结果:
   找到 3 条相关内容 - 技术规格要求：供应商应具备ISO9001质量管理体系认证...
```

## 6. 实现步骤

1. 修改后端 `bid_review_agent.py`，SSE 事件新增 `tool_args` 和 `tool_result`
2. 修改前端 `frontend/src/types/index.ts`，扩展 SSEEvent 和 ToolResult 类型
3. 修改前端 `frontend/src/components/ReviewTimeline.vue`：
   - 添加工具配置映射表
   - 添加 handleSSEEvent 中的 tool_args 和 tool_result 提取
   - 修改工具调用节点的展示模板，使用友好描述

## 7. 涉及的工具

| 工具名 | 参数 | 结果格式化 |
|--------|------|-----------|
| search_tender_doc | doc_type, query, chunk, full_content | 找到 N 条相关内容 - 内容摘要... |
| rag_search | query, limit | 知识库返回 N 条结果 |
| comparator | requirement, bid_content | 比对完成: 结果 |
