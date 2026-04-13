# Review Execution 页面 LeftPane 重构设计

**日期**: 2026-04-13
**状态**: 已批准
**目标**: 将现有时间线列表重构为原型 bidding_review_todo_tasklist.html 的多代理执行 UI

---

## 1. 概述与目标

将 `/review-execution` 页面的 `LeftPane.vue` 从简单的 SSE 步骤时间线重构为支持多代理并行执行的完整 UI，包含：
- 主代理解析输出块
- 待办任务列表
- 子代理折叠卡片
- 合并质检阶段

## 2. 整体架构

```
LeftPane (重构后)
├── run-header         # 项目信息 + 状态徽章
├── ExecutionStepper   # 复用现有组件 (4阶段)
├── MasterOutputBlock  # 主代理解析输出 (新增)
│   └── MasterAgentOutput
├── TodoList           # 待办任务列表 (新增)
│   └── TodoItem (×4)
├── SubAgentTimeline   # 子代理折叠卡片 (重构)
│   └── SubAgentCard (×N)
└── MergeBlock         # 合并阶段 (新增)
    └── MergeStep (×5)
```

## 3. 组件设计

### 3.1 MasterOutputBlock

**用途**: 展示主代理解析规则库的实时输出

**Props**:
```ts
interface MasterOutputBlockProps {
  steps: TimelineStep[]  // step_type === 'master' 的步骤
}
```

**样式**:
- 背景: `var(--bg1)`, 边框: `var(--line)`, 圆角: `var(--r2)`
- Header 带紫色 chip: `chip-master`
- 输出行格式: `› cmd` 形式，高亮关键词

**示例输出**:
```
› Scanning /rules/政府采购/ → found 4 rule documents
› Parsing rule_001_资质要求.md → 5 items, deps: sequential
```

### 3.2 TodoList

**用途**: 展示4个规则文档对应的任务卡片

**Props**:
```ts
interface TodoItem {
  id: string
  name: string           // 任务名称
  ruleFile: string       // 规则文件名
  checkItemsCount: number // 检查项数量
  depsType: 'sequential' | 'branching'
  status: 'done' | 'running' | 'wait'
  agentId: string        // A1, A2, A3, A4
}
```

**样式**:
- 任务项: `todo-item td-done/td-run/td-wait`
- 勾选框: 16×16, 圆角 3px
- Agent 标签: `tag-done/tag-run/tag-wait`

### 3.3 SubAgentCard

**用途**: 可折叠展开的子代理卡片

**Props**:
```ts
interface SubAgentCardProps {
  agentId: string
  title: string
  ruleFile: string
  checkItems: CheckItem[]
  status: 'done' | 'running' | 'wait'
  findings: Finding[]
  runningLog?: string
}

interface CheckItem {
  name: string
  status: 'done' | 'run' | 'wait' | 'fail'
}

interface Finding {
  type: 'crit' | 'major' | 'pass'
  text: string
}
```

**样式**:
- 卡片头部: `agent-card-head`, 可点击展开
- 进度条: 72×3px, `pbar-inner`
- 执行链: `dep-chain`, 胶囊形节点 `dp-done/dp-run/dp-wait/dp-fail`
- Findings: `findings`, 标签 `ft-crit/ft-major/ft-pass`
- 运行日志: `run-log`, 带闪烁光标

**交互**:
- 点击头部展开/折叠
- Space 键展开/折叠所有卡片
- R 键刷新状态

### 3.4 MergeBlock

**用途**: 展示合并质检阶段的5个步骤

**样式**:
- 整体半透明 (opacity: 0.5)
- 5个步骤横向排列: 汇总 → 去重 → 排序 → 校验 → 报告
- 步骤间用箭头连接

## 4. 样式系统

使用原型定义的 CSS 变量:

```css
--green: #3dd68c;     /* 完成 / 通过 */
--amber: #f0a429;    /* 待办 / 一般缺陷 */
--blue: #4da6ff;     /* 子代理 */
--purple: #a78bfa;   /* 主代理 / 运行中 */
--teal: #2dd4bf;     /* 合并阶段 */
--red: #f87171;      /* 严重缺陷 */
```

## 5. 数据流

```
SSE Event ──► ReviewExecutionView.handleSSEEvent()
                    │
                    ├── phase (状态更新)
                    │
                    ├── steps[] (完整步骤列表)
                    │
                    └── LeftPane (重构后)
                          │
                          ├── MasterOutputBlock (steps.filter(step_type==='master'))
                          ├── TodoList (从 steps 解析出任务元信息)
                          ├── SubAgentTimeline (steps.filter(step_type==='sub_agent'))
                          └── MergeBlock (phase === 'merging' 时显示)
```

## 6. SSE 事件扩展

当前 SSE step 事件结构:
```ts
{
  step_number: number,
  step_type: 'master' | 'sub_agent' | 'observation' | 'thought' | 'tool_call',
  content: string,
  timestamp: Date,
  tool_calls?: ToolCall[],
  tool_results?: ToolResult[]
}
```

**需要的扩展字段**:
- `agent_id`: 子代理标识 (A1/A2/A3/A4)
- `rule_file`: 关联的规则文件
- `check_items`: 检查项列表
- `findings`: 发现项列表
- `deps_type`: 依赖类型

## 7. 键盘快捷键

| 快捷键 | 功能 |
|--------|------|
| Space | 展开/折叠所有 SubAgentCard |
| R | 刷新 SSE 连接 |

## 8. 实现顺序

1. **SubAgentCard** - 核心组件，承载执行链和 findings
2. **SubAgentTimeline** - 管理多个 SubAgentCard
3. **TodoList** - 任务卡片列表
4. **MasterOutputBlock** - 主代理输出
5. **MergeBlock** - 合并阶段
6. **LeftPane 整合** - 组装所有组件
7. **键盘快捷键** - Space/R

## 9. 文件变更

**新增文件**:
- `frontend/src/components/execution/SubAgentCard.vue`
- `frontend/src/components/execution/SubAgentTimeline.vue`
- `frontend/src/components/execution/TodoList.vue`
- `frontend/src/components/execution/MasterOutputBlock.vue`
- `frontend/src/components/execution/MergeBlock.vue`

**修改文件**:
- `frontend/src/components/execution/LeftPane.vue` - 重构为组装组件
- `frontend/src/views/ReviewExecutionView.vue` - SSE 事件处理逻辑调整
