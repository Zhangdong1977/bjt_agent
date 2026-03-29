# Timeline UI 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化 ReviewTimeline.vue 组件，实现渐变背景卡片、节编号标签、可折叠详情面板、耗时显示

**Architecture:** 保持 SSE 数据流不变，扩展 TimelineStep 接口增加 duration/tool_params/tool_result 字段，使用 Ant Design Collapse 实现详情折叠

**Tech Stack:** Vue 3, TypeScript, ant-design-vue, CSS

---

## 文件结构

```
frontend/src/
├── components/
│   └── ReviewTimeline.vue     # 修改: 扩展功能 + 渐变卡片 + Collapse
└── types/
    └── index.ts              # 可能需要: 扩展 SSEEvent/TimelineStep 类型
```

---

## Task 1: 扩展 TimelineStep 接口

**Files:**
- Modify: `frontend/src/components/ReviewTimeline.vue:18-25`

- [ ] **Step 1: 扩展 TimelineStep 接口**

将现有接口:

```typescript
interface TimelineStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
  status?: 'pending' | 'running' | 'completed' | 'error'
}
```

替换为:

```typescript
interface TimelineStep {
  step_number: number
  step_type: string
  tool_name?: string
  content: string
  timestamp: Date
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number      // 耗时（秒）
  tool_params?: {       // 工具调用参数
    prompt: string
  }
  tool_result?: string   // 工具调用结果
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/ReviewTimeline.vue
git commit -m "feat(timeline): extend TimelineStep interface with duration and tool_params"
```

---

## Task 2: 添加 Tag 和 Collapse 导入

**Files:**
- Modify: `frontend/src/components/ReviewTimeline.vue:1-13`

- [ ] **Step 1: 添加 Tag 和 Collapse 导入**

在现有的 ant-design/icons-vue 导入后添加:

```typescript
import { Tag, Collapse, CollapsePanel } from 'ant-design-vue'
```

- [ ] **Step 2: 添加 Collapse 样式导入**

确认 ant-design-vue 的 collapse 样式已通过 main.ts 全局引入，或手动导入:

```typescript
import 'ant-design-vue/es/collapse/style/css'
```

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/ReviewTimeline.vue
git commit -m "feat(timeline): add Tag and Collapse imports from ant-design-vue"
```

---

## Task 3: 添加辅助方法

**Files:**
- Modify: `frontend/src/components/ReviewTimeline.vue:73-88` (getStepColor 区域)

- [ ] **Step 1: 添加 getTagColor 方法**

在 `getStepColor` 方法后添加:

```typescript
function getTagColor(stepType: string): string {
  const colorMap: Record<string, string> = {
    tool_call: 'purple',
    observation: 'green',
    thought: 'blue',
  }
  return colorMap[stepType] || 'default'
}
```

- [ ] **Step 2: 添加 getStepEmoji 方法**

```typescript
function getStepEmoji(stepType: string): string {
  const emojiMap: Record<string, string> = {
    tool_call: '🔧',
    observation: '👁',
    thought: '💭',
  }
  return emojiMap[stepType] || '📝'
}
```

- [ ] **Step 3: 添加 getStepLabel 方法**

```typescript
function getStepLabel(stepType: string, toolName?: string): string {
  if (stepType === 'tool_call') {
    return `工具调用: ${toolName || 'unknown'}`
  }
  if (stepType === 'observation') {
    return '观察'
  }
  return '思考过程'
}
```

- [ ] **Step 4: 添加 formatTime 方法**

```typescript
function formatTime(date: Date): string {
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
```

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/ReviewTimeline.vue
git commit -m "feat(timeline): add helper methods for tag color, emoji, label and time formatting"
```

---

## Task 4: 重写模板实现新UI

**Files:**
- Modify: `frontend/src/components/ReviewTimeline.vue:96-134`

- [ ] **Step 1: 替换模板内容**

将现有的 `<a-timeline>` 模板内容:

```vue
<a-timeline mode="left">
  <a-timeline-item
    v-for="(step, index) in steps"
    :key="index"
    :color="getStepColor(step.step_type)"
    :pending="step.status === 'running'"
  >
    <template #dot>
      <component :is="getStepIcon(step)" :class="{ 'spin-icon': step.status === 'running' }" />
    </template>

    <div :class="['timeline-content-card', `card-${step.step_type}`]">
      <div class="card-header">
        <span :class="['step-icon', `icon-${step.step_type}`]">
          <ToolOutlined v-if="step.step_type === 'tool_call'" />
          <EyeOutlined v-else-if="step.step_type === 'observation'" />
          <BulbOutlined v-else />
        </span>
        <span class="step-type">
          {{ step.step_type === 'tool_call' ? `${step.tool_name || '工具'}` : step.step_type === 'observation' ? '观察' : '思考' }}
        </span>
      </div>
      <p class="step-text">{{ step.content }}</p>
    </div>
  </a-timeline-item>

  <a-timeline-item v-if="steps.length === 0" pending>
    <template #dot><ClockCircleOutlined /></template>
    <div class="timeline-empty">
      <span>等待智能体启动...</span>
    </div>
  </a-timeline-item>
</a-timeline>
```

替换为:

```vue
<a-timeline mode="left" class="review-timeline">
  <a-timeline-item
    v-for="(step, index) in steps"
    :key="index"
    :color="step.status === 'running' ? 'blue' : getStepColor(step.step_type)"
    :pending="step.status === 'running'"
  >
    <template #dot>
      <component :is="getStepIcon(step)" :class="{ 'spin-icon': step.status === 'running' }" />
    </template>

    <div :class="['timeline-content-card', `card-${step.step_type}`]">
      <!-- 卡片头部 -->
      <div class="card-header">
        <Tag :color="getTagColor(step.step_type)">第 {{ step.step_number }} 节</Tag>
        <span class="step-label">
          {{ getStepEmoji(step.step_type) }} {{ getStepLabel(step.step_type, step.tool_name) }}
        </span>
        <span v-if="step.status === 'running'" class="status-running">
          <Tag color="processing">RUNNING</Tag>
        </span>
        <span v-if="step.duration" class="duration">{{ step.duration }}s</span>
        <span class="timestamp">{{ formatTime(step.timestamp) }}</span>
      </div>

      <!-- 步骤内容 -->
      <p class="step-text">{{ step.content }}</p>

      <!-- 可折叠详细信息（仅工具调用有此项） -->
      <Collapse v-if="step.step_type === 'tool_call' && step.tool_params" class="tool-collapse" ghost>
        <CollapsePanel key="1" :header="'显示详细信息'">
          <div class="tool-params">
            <strong>提示词:</strong>
            <div class="prompt-box">{{ step.tool_params.prompt }}</div>
          </div>
          <div v-if="step.tool_result" class="tool-result">
            <strong>结果:</strong>
            <div>{{ step.tool_result }}</div>
          </div>
        </CollapsePanel>
      </Collapse>
    </div>
  </a-timeline-item>

  <a-timeline-item v-if="steps.length === 0" pending>
    <template #dot><ClockCircleOutlined /></template>
    <div class="timeline-empty">
      <span>等待智能体启动...</span>
    </div>
  </a-timeline-item>
</a-timeline>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/components/ReviewTimeline.vue
git commit -m "refactor(timeline): rewrite template with section tags, collapse details and duration display"
```

---

## Task 5: 更新 CSS 样式实现渐变背景

**Files:**
- Modify: `frontend/src/components/ReviewTimeline.vue:136-268`

- [ ] **Step 1: 更新 .timeline-content-card 样式**

将现有的卡片样式:

```css
.timeline-content-card {
  flex: 1;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  background: #fff;
  border-left: 4px solid;
  margin-bottom: 0.75rem;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.timeline-content-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.card-tool_call {
  border-left-color: #fa8c16;
}

.card-observation {
  border-left-color: #52c41a;
}

.card-thought {
  border-left-color: #1890ff;
}
```

替换为:

```css
.timeline-content-card {
  flex: 1;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  margin-bottom: 0.75rem;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.timeline-content-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

/* 渐变背景卡片 */
.card-tool_call {
  background: linear-gradient(135deg, rgb(249, 240, 255) 0%, rgb(253, 250, 255) 100%);
  border-left: 4px solid rgb(211, 173, 247);
}

.card-observation {
  background: linear-gradient(135deg, rgb(246, 255, 250) 0%, rgb(250, 255, 252) 100%);
  border-left: 4px solid rgb(183, 235, 200);
}

.card-thought {
  background: linear-gradient(135deg, rgb(240, 248, 255) 0%, rgb(245, 250, 255) 100%);
  border-left: 4px solid rgb(187, 224, 255);
}
```

- [ ] **Step 2: 添加新的样式类**

在 `.card-thought` 样式后添加:

```css
/* 头部样式 */
.card-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.step-label {
  font-weight: 600;
  color: #333;
}

.status-running {
  margin-left: auto;
}

.duration {
  color: rgb(153, 153, 153);
  font-size: 0.85rem;
}

.timestamp {
  color: rgb(153, 153, 153);
  font-size: 0.85rem;
}

/* 折叠面板样式 */
.tool-collapse {
  margin-top: 0.75rem;
  border-radius: 4px;
}

.tool-params {
  margin-bottom: 0.75rem;
}

.tool-params strong {
  display: block;
  margin-bottom: 0.25rem;
  font-size: 0.85rem;
  color: #666;
}

.prompt-box {
  padding: 8px;
  background: rgb(245, 245, 245);
  border-radius: 4px;
  font-size: 12px;
  color: rgb(24, 144, 255);
  white-space: pre-wrap;
}

.tool-result {
  margin-top: 0.5rem;
}

.tool-result strong {
  display: block;
  margin-bottom: 0.25rem;
  font-size: 0.85rem;
  color: #666;
}
```

- [ ] **Step 3: 删除不再需要的样式**

删除以下样式（已迁移到新的卡片样式中）:
- `.step-icon` (图标已移至 Ant Design Timeline dot)
- `.icon-tool_call`, `.icon-observation`, `.icon-thought`
- `.card-header` 旧的 `display: flex` 定义

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/ReviewTimeline.vue
git commit -m "style(timeline): add gradient backgrounds and new component styles"
```

---

## Task 6: 验证和测试

- [ ] **Step 1: 启动前端开发服务器**

```bash
cd frontend && npm run dev
```

- [ ] **Step 2: 访问时间线页面**

导航到任意项目的审查时间线页面

- [ ] **Step 3: 验证视觉呈现**

- [ ] Timeline 左侧布局正确
- [ ] 不同步骤类型显示正确渐变背景（tool_call=紫, observation=绿, thought=蓝）
- [ ] 显示节编号 Tag（第 X 节）
- [ ] pending/running 状态显示蓝色脉冲动画
- [ ] 卡片有悬停提升效果
- [ ] 耗时显示（如 "1.4s"）
- [ ] 时间戳显示（HH:mm:ss）

- [ ] **Step 4: 验证折叠功能**

对于 tool_call 类型的步骤:
- [ ] 显示"显示详细信息"折叠面板
- [ ] 点击可正确展开/收起
- [ ] 展开后显示提示词和结果

- [ ] **Step 5: 验证 SSE 功能**

触发一个审查任务，检查时间线是否正确更新

---

## 验证清单

| 验证项 | 预期行为 |
|--------|---------|
| 布局 | Timeline 在左侧显示，内容在右侧 |
| tool_call 渐变 | 紫色渐变背景 + 粉色左边框 |
| observation 渐变 | 绿色渐变背景 + 浅绿左边框 |
| thought 渐变 | 蓝色渐变背景 + 浅蓝左边框 |
| 节编号 | 紫色 Tag 显示"第 X 节" |
| pending 动画 | 蓝色脉冲效果 |
| 耗时显示 | 灰色"Xs"格式 |
| 时间戳 | 灰色 HH:mm:ss 格式 |
| 折叠详情 | 点击展开显示提示词和结果 |

---

## 风险与限制

1. **Ant Design Collapse ghost mode**: `ghost` 属性可能影响折叠面板样式，需测试
2. **渐变背景兼容性**: 确认在目标浏览器上渐变显示正常
3. **SSE 数据完整性**: 后端需确保发送 `duration`、`tool_params`、`tool_result` 字段
