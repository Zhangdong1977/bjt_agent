# 时间线工具调用节点合并设计方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** 将时间线上分离的 tool_call 和 tool_result 节点合并为一个节点展示

**背景:** 当前实现中，工具调用在时间线上显示为两个独立节点（tool_call 和 tool_result），用户体验不够流畅。

**Architecture:** 前端事件处理层合并 — handleSSEEvent 将 tool_result 合并到对应的 tool_call 节点

**Tech Stack:** Vue3/TypeScript (前端)

---

## 1. 数据流变化

### 当前数据流
```
后端发送:
  → step event (tool_call, step_number=5, tool_args)
  → step event (tool_result, step_number=6, tool_result)

前端处理:
  → step_number=5 创建独立节点
  → step_number=6 创建独立节点
```

### 合并后数据流
```
后端发送: (不变)
  → step event (tool_call, step_number=5, tool_args)
  → step event (tool_result, step_number=6, tool_result)

前端处理:
  → step_number=5 创建 TimelineStep，包含 tool_args
  → step_number=6 找到 step_number=5 的节点，补充 tool_result
  → step_number=6 的节点不创建（或标记为已合并）
```

---

## 2. 配对策略

**核心假设:**
1. 后端保证 `tool_result` 紧跟在对应 `tool_call` 之后
2. 相邻的 `tool_call` 和 `tool_result` 具有相同的 `tool_name`

**配对逻辑:**
```typescript
function handleSSEEvent(event: SSEEvent) {
  if (event.type === 'step' && event.step_number !== undefined) {
    if (event.step_type === 'tool_call') {
      // tool_call: 创建新节点
      steps.value.push({
        step_number: event.step_number,
        step_type: 'tool_call',  // 保留原始类型
        tool_name: event.tool_name,
        content: event.content || '',
        timestamp: new Date(),
        tool_args: event.tool_args,
        tool_result: undefined,  // 预置，后续补充
      })
    } else if (event.step_type === 'tool_result') {
      // tool_result: 查找对应的 tool_call 节点并合并
      const pairedStep = findPairedToolCall(event.tool_name, event.step_number)
      if (pairedStep) {
        pairedStep.tool_result = event.tool_result
        // 可选：标记 tool_result 节点不渲染
      }
    }
  }
}

function findPairedToolCall(toolName: string | undefined, currentStepNumber: number): TimelineStep | null {
  // 查找最近的前一个 tool_call 节点（step_number = currentStepNumber - 1）
  // 且 tool_name 相同
  return steps.value.find(s =>
    s.step_number === currentStepNumber - 1 &&
    s.tool_name === toolName &&
    s.step_type === 'tool_call'
  ) || null
}
```

---

## 3. TimelineStep 数据结构

**不变** — 当前结构已支持合并展示：
```typescript
interface TimelineStep {
  step_number: number
  step_type: string           // 保留 'tool_call' 作为类型标识
  tool_name?: string
  content: string
  timestamp: Date
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number
  tool_args?: Record<string, any>    // 调用参数
  tool_result?: ToolResult             // 返回结果
}
```

---

## 4. 展示模板变化

### 当前模板（两个独立节点）
```vue
<!-- tool_call 节点 -->
<div v-if="step.step_type === 'tool_call'">
  <Collapse>调用参数</Collapse>
</div>

<!-- tool_result 节点 -->
<div v-if="step.step_type === 'tool_result'">
  <Collapse>返回结果</Collapse>
</div>
```

### 合并后模板（一个节点，上下分区）
```vue
<div v-if="step.step_type === 'tool_call'" class="tool-node">
  <!-- 节点头部：工具名称 + 状态 -->
  <div class="tool-header">
    <Tag color="purple">第 {{ step.step_number }} 节</Tag>
    <span class="tool-name">{{ getFriendlyToolName(step.tool_name) }}</span>
    <Tag v-if="step.tool_result" :color="step.tool_result.status === 'success' ? 'green' : 'red'">
      {{ step.tool_result.status === 'success' ? '成功' : '失败' }}
    </Tag>
  </div>

  <!-- 调用参数区域 -->
  <div class="tool-section call-section">
    <strong>调用参数:</strong>
    <div class="params-list">
      <div v-for="param in getFriendlyArgs(step.tool_name, step.tool_args)" :key="param.key">
        {{ param.key }}: {{ param.value }}
      </div>
    </div>
  </div>

  <!-- 返回结果区域（工具执行完成后显示） -->
  <div v-if="step.tool_result" class="tool-section result-section">
    <strong>返回结果:</strong>
    <div class="result-text">{{ getFriendlyResult(step.tool_name, step.tool_result) }}</div>
  </div>
</div>

<!-- 不再单独渲染 tool_result 节点 -->
<div v-if="step.step_type === 'tool_result' && !isMerged(step)" class="tool-node">
  ...
</div>
```

---

## 5. 样式设计

### 合并节点样式
```css
.tool-node {
  background: linear-gradient(135deg, rgb(249, 240, 255) 0%, rgb(253, 250, 255) 100%);
  border-left: 4px solid rgb(211, 173, 247);
  border-radius: 6px;
  padding: 0.75rem 1rem;
}

.tool-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.tool-name {
  font-weight: 600;
  font-size: 1rem;
}

.call-section {
  margin-bottom: 0.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px dashed #ddd;
}

.result-section {
  background: rgba(82, 196, 26, 0.1);
  border-radius: 4px;
  padding: 0.5rem;
}
```

### 合并效果示意
```
┌─────────────────────────────────────┐
│ 🔧 第 5 节  搜索文档        [成功]   │  ← 节点头部
├─────────────────────────────────────┤
│ 调用参数:                           │  ← 调用区域
│   文档类型: 招标文档                 │
│   查询内容: 质量要求                 │
├─────────────────────────────────────┤
│ 返回结果:                           │  ← 返回区域
│   找到 3 条相关内容 - 文档片段...    │
└─────────────────────────────────────┘
```

---

## 6. 需修改的文件

| 文件 | 职责 |
|------|------|
| `frontend/src/components/ReviewTimeline.vue` | handleSSEEvent 配对逻辑 + 模板合并展示 |
| `frontend/src/stores/project.ts` | 历史步骤加载时的配对处理（如需要） |

---

## 7. 边界情况处理

1. **tool_result 先到达**：理论上不会发生，后端保证顺序
2. **配对失败**：如果找不到对应的 tool_call，将 tool_result 作为独立节点处理
3. **历史步骤加载**：加载时 `loadHistoricalSteps` 也需要执行配对逻辑
4. **工具执行中（tool_result 未到达）**：节点只显示调用参数，状态标记为 "进行中"
