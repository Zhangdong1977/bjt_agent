# Timeline UI 优化设计

**日期:** 2026-03-29
**状态:** 已批准
**类型:** 前端组件优化

## 概述

优化 `ReviewTimeline.vue` 组件的视觉效果，采用步骤级组织、点击展开详情、渐变背景卡片，与项目现有 Ant Design 风格保持一致。

## 设计决策

### 1. 布局模式
- **选择:** 步骤级组织，每个步骤作为独立时间线节点
- **原因:** 信息更细粒度，用户可追踪每一步操作

### 2. 卡片样式
- **渐变背景 + 左边框**
- 背景色根据步骤类型：
  - `tool_call`: 紫色渐变 `rgb(249, 240, 255)` + 边框 `rgb(211, 173, 247)`
  - `observation`: 绿色系渐变
  - `thought`: 蓝色系渐变
- **原因:** 视觉区分度高，与 Ant Design 风格一致

### 3. 运行状态可视化
- 使用 Ant Design Timeline 的 `pending` 属性实现蓝色脉冲动画
- 旋转加载图标表示进行中
- **原因:** 内置动画流畅，与 Ant Design 原生组件一致

### 4. 详情展示
- 使用 `<a-collapse>` 组件封装工具调用详情
- 点击"显示详细信息"展开/收起
- **原因:** 节省空间，信息按需获取

### 5. 辅助信息
- 节编号标签（`<a-tag>` 第 X 节）
- 时间戳（时分秒）
- 耗时显示（如 "1.4s"）

## 组件结构

```vue
<a-timeline mode="left">
  <a-timeline-item
    v-for="step in steps"
    :key="index"
    :color="step.status === 'running' ? 'blue' : getStepColor(step.step_type)"
    :pending="step.status === 'running'"
  >
    <template #dot>
      <component :is="getStepIcon(step)" :class="{ 'spin-icon': step.status === 'running' }" />
    </template>

    <div class="timeline-content-card" :class="`card-${step.step_type}`">
      <!-- 卡片头部 -->
      <div class="card-header">
        <a-tag :color="getTagColor(step.step_type)">第 {{ step.step_number }} 节</a-tag>
        <span class="step-label">
          {{ getStepEmoji(step.step_type) }} {{ getStepLabel(step) }}
        </span>
        <span v-if="step.tool_name" class="tool-name">{{ step.tool_name }}</span>
        <span v-if="step.duration" class="duration">{{ step.duration }}s</span>
        <span class="timestamp">{{ formatTime(step.timestamp) }}</span>
      </div>

      <!-- 步骤内容 -->
      <p class="step-text">{{ step.content }}</p>

      <!-- 可折叠详细信息（仅工具调用有此项） -->
      <a-collapse v-if="step.step_type === 'tool_call' && step.tool_params" ghost>
        <a-collapse-panel key="1" header="显示详细信息">
          <div class="tool-params">
            <strong>提示词:</strong>
            <div class="prompt-box">{{ step.tool_params.prompt }}</div>
          </div>
          <div v-if="step.tool_result" class="tool-result">
            <strong>结果:</strong>
            <div>{{ step.tool_result }}</div>
          </div>
        </a-collapse-panel>
      </a-collapse>
    </div>
  </a-timeline-item>
</a-timeline>
```

## 数据流

### SSE 事件处理
```
step 类型事件 → 追加到 steps 数组
status=running 事件 → 清空 steps，重新开始
```

### 新增字段
```typescript
interface TimelineStep {
  // ... 现有字段
  duration?: number    // 耗时（秒）
  tool_params?: {     // 工具调用参数
    prompt: string
  }
  tool_result?: string // 工具调用结果
}
```

## 颜色方案

| 步骤类型 | 卡片背景 | 左边框 | Tag颜色 |
|---------|---------|--------|---------|
| tool_call | 紫色渐变 | rgb(211, 173, 247) | purple |
| observation | 绿色渐变 | rgb(183, 235, 200) | green |
| thought | 蓝色渐变 | rgb(187, 224, 255) | blue |

## 需修改的代码

### frontend/src/components/ReviewTimeline.vue

1. **导入新增组件**
   - `a-tag`, `a-collapse`, `a-collapse-panel`

2. **新增方法**
   - `getTagColor(stepType)` - 返回 Tag 颜色
   - `getStepEmoji(stepType)` - 返回 emoji 图标
   - `formatTime(date)` - 格式化时间戳为 HH:mm:ss
   - `getDuration(step)` - 计算步骤耗时

3. **更新模板**
   - 添加节编号 Tag
   - 添加工具名、耗时、时间戳显示
   - 用 Collapse 包裹工具调用详情

4. **更新样式**
   - 添加渐变背景样式
   - 调整卡片内边距和间距

### frontend/src/types/index.ts (如需要)

扩展 `SSEEvent` 或 `TimelineStep` 接口添加新字段

## 测试要点

- [ ] Timeline 在 left 模式下正确显示
- [ ] 不同步骤类型显示对应渐变背景
- [ ] pending 状态显示蓝色脉冲动画
- [ ] 点击"显示详细信息"正确展开/收起
- [ ] 耗时显示正确计算
- [ ] SSE 事件正常更新时间线
