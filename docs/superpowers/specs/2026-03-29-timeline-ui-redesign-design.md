# Timeline UI 改版设计

**日期:** 2026-03-29
**状态:** 已批准
**类型:** 前端组件重构

## 概述

将 `ReviewTimeline.vue` 组件从自定义 CSS Timeline 迁移至 Ant Design Vue Timeline 组件，提升视觉效果并保持现有功能。

## 当前实现

- 文件: `frontend/src/components/ReviewTimeline.vue`
- 使用自定义 CSS 实现时间线布局
- 已导入 Ant Design Icons
- 支持三种步骤类型: `tool_call`, `observation`, `thought`
- 支持四种状态: `pending`, `running`, `completed`, `error`

## 设计决策

### 1. 布局模式
- **选择:** `mode="left"` (左侧布局)
- **原因:** 与当前布局一致，用户习惯无需改变

### 2. 颜色方案
按步骤类型着色：

| 步骤类型 | 颜色代码 | 用途 |
|---------|---------|------|
| tool_call | `#fa8c16` | 工具调用节点 |
| observation | `#52c41a` | 观察节点 |
| thought | `#1890ff` | 思考节点 |

### 3. Pending 动画
- **选择:** 使用 Ant Design Timeline 的 `pending` 属性
- **原因:** 内置脉冲动画，效果流畅

### 4. 卡片样式
- **选择:** 保留现有卡片样式
- **原因:** 保持内容可读性和视觉层次

## 组件结构

```
<a-timeline mode="left">
  <a-timeline-item
    v-for="step in steps"
    :key="index"
    :color="getStepColor(step.step_type)"
    :pending="step.status === 'running'"
  >
    <template #dot>
      <component :is="getStepIcon(step)" />
    </template>
    <div class="timeline-content-card" :class="`card-${step.step_type}`">
      <div class="card-header">
        <span class="step-icon" :class="`icon-${step.step_type}`">
          <ToolOutlined v-if="step.step_type === 'tool_call'" />
          <EyeOutlined v-else-if="step.step_type === 'observation'" />
          <BulbOutlined v-else />
        </span>
        <span class="step-type">
          {{ getStepLabel(step) }}
        </span>
      </div>
      <p class="step-text">{{ step.content }}</p>
    </div>
  </a-timeline-item>
</a-timeline>
```

## 数据流

SSE 事件处理逻辑保持不变：

1. `step` 类型事件 → 追加到 `steps` 数组
2. `status=running` 事件 → 清空 steps，重新开始

## 需修改的代码

### frontend/src/components/ReviewTimeline.vue

1. 导入 `Timeline` 和 `TimelineItem` 组件
2. 替换模板中的自定义 timeline-item 为 a-timeline-item
3. 添加 `getStepColor()` 方法
4. 添加 `getStepLabel()` 方法
5. 保留现有的 `<style scoped>` 中的卡片样式

## 测试要点

- [ ] Timeline 在 left 模式下正确显示
- [ ] 不同步骤类型显示正确颜色
- [ ] pending 状态显示脉冲动画
- [ ] 卡片样式保留悬停效果
- [ ] SSE 事件正常更新时间线
