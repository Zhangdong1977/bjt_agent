# Review Results and Timeline Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将项目详情页中的"审查结果"和"时间线"按数据粒度分离 - 审查结果为项目级别（始终显示最新），时间线为任务级别（随历史任务选择变化）

**Architecture:** 组件分离方案 - 创建 `ReviewResultsArea.vue` 和 `TimelineArea.vue` 两个独立组件

**Tech Stack:** Vue3, TypeScript, Element Plus, Pinia Store

---

## File Structure

```
frontend/src/components/
├── ReviewResultsArea.vue     # 新增：项目级别审查结果组件
└── TimelineArea.vue          # 新增：任务级别时间线组件（含任务选择器）

frontend/src/views/
└── ProjectView.vue           # 修改：整合新组件，移除旧的混合逻辑

frontend/src/types/
└── index.ts                  # 确认类型定义（已有 ReviewTaskListItem）
```

---

## Task 1: Create ReviewResultsArea.vue Component

**Files:**
- Create: `frontend/src/components/ReviewResultsArea.vue`

### Steps:

- [ ] **Step 1: Create ReviewResultsArea.vue component**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'

const projectStore = useProjectStore()

const reviewResults = computed(() => projectStore.reviewResults)
const hasResults = computed(() => reviewResults.value && reviewResults.value.findings.length > 0)

function getSeverityClass(severity: string) {
  switch (severity) {
    case 'critical': return 'severity-critical'
    case 'major': return 'severity-major'
    case 'minor': return 'severity-minor'
    default: return ''
  }
}
</script>

<template>
  <div class="review-results-area">
    <div v-if="hasResults" class="summary">
      <div class="summary-item">
        <span class="summary-value">{{ reviewResults.summary.total_requirements }}</span>
        <span class="summary-label">总计</span>
      </div>
      <div class="summary-item success">
        <span class="summary-value">{{ reviewResults.summary.compliant }}</span>
        <span class="summary-label">合规</span>
      </div>
      <div class="summary-item error">
        <span class="summary-value">{{ reviewResults.summary.non_compliant }}</span>
        <span class="summary-label">不合规</span>
      </div>
      <div class="summary-item critical">
        <span class="summary-value">{{ reviewResults.summary.critical }}</span>
        <span class="summary-label">严重</span>
      </div>
      <div class="summary-item major">
        <span class="summary-value">{{ reviewResults.summary.major }}</span>
        <span class="summary-label">主要</span>
      </div>
      <div class="summary-item minor">
        <span class="summary-value">{{ reviewResults.summary.minor }}</span>
        <span class="summary-label">次要</span>
      </div>
    </div>

    <div v-if="hasResults" class="findings-list">
      <div
        v-for="finding in reviewResults.findings"
        :key="finding.id"
        :class="['finding-card', { 'non-compliant': !finding.is_compliant }]"
      >
        <div class="finding-header">
          <span :class="['severity-badge', getSeverityClass(finding.severity)]">
            {{ finding.severity }}
          </span>
          <span :class="['compliance-badge', finding.is_compliant ? 'compliant' : 'non-compliant']">
            {{ finding.is_compliant ? '合规' : '不合规' }}
          </span>
        </div>
        <div class="finding-body">
          <p class="requirement"><strong>要求:</strong> {{ finding.requirement_content }}</p>
          <p class="bid-content"><strong>应标内容:</strong> {{ finding.bid_content }}</p>
          <p v-if="finding.explanation" class="explanation">{{ finding.explanation }}</p>
          <p v-if="finding.suggestion && !finding.is_compliant" class="suggestion">
            <strong>建议:</strong> {{ finding.suggestion }}
          </p>
        </div>
      </div>
    </div>

    <div v-if="!hasResults" class="no-results">
      <p>暂无审查结果</p>
    </div>
  </div>
</template>

<style scoped>
.review-results-area {
  /* 复用现有样式 */
}

.summary {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}

.summary-item {
  text-align: center;
  padding: 1rem;
  background: #f5f5f5;
  border-radius: 8px;
}

.summary-value {
  display: block;
  font-size: 2rem;
  font-weight: bold;
  color: #333;
}

.summary-label {
  color: #666;
  font-size: 0.9rem;
}

.summary-item.success .summary-value { color: #68d391; }
.summary-item.error .summary-value { color: #e53e3e; }
.summary-item.critical .summary-value { color: #c53030; }
.summary-item.major .summary-value { color: #dd6b20; }
.summary-item.minor .summary-value { color: #d69e2e; }

.findings-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.finding-card {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 1rem;
}

.finding-card.non-compliant {
  border-color: #fc8181;
  background: #fff5f5;
}

.finding-header {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.severity-badge, .compliance-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
}

.severity-critical { background: #c53030; color: white; }
.severity-major { background: #dd6b20; color: white; }
.severity-minor { background: #d69e2e; color: white; }

.compliance-badge.compliant { background: #68d391; color: white; }
.compliance-badge.non-compliant { background: #fc8181; color: white; }

.finding-body p {
  margin: 0.5rem 0;
  color: #333;
}

.explanation {
  color: #666;
  font-style: italic;
}

.suggestion {
  color: #6366f1;
}

.no-results {
  text-align: center;
  padding: 2rem;
  color: #666;
}
</style>
```

- [ ] **Step 2: Commit ReviewResultsArea.vue**

```bash
git add frontend/src/components/ReviewResultsArea.vue
git commit -m "feat(frontend): add ReviewResultsArea component for project-level review results"
```

---

## Task 2: Create TimelineArea.vue Component

**Files:**
- Create: `frontend/src/components/TimelineArea.vue`

### Steps:

- [ ] **Step 1: Create TimelineArea.vue component**

```vue
<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useProjectStore } from '@/stores/project'
import { reviewApi } from '@/api/client'
import ReviewTimeline from '@/components/ReviewTimeline.vue'
import { ElMessage } from 'element-plus'

const props = defineProps<{
  projectId: string
}>()

const emit = defineEmits<{
  (e: 'task-complete', taskId: string): void
}>()

const projectStore = useProjectStore()
const timelineRef = ref<InstanceType<typeof ReviewTimeline> | null>(null)

const completedTasks = computed(() =>
  projectStore.reviewTasks.filter(t => t.status === 'completed')
)

const selectedTaskId = ref<string>('')
const historicalSteps = ref<any[]>([])
const isHistoricalMode = ref(false)
const canStartReview = computed(() => {
  const tenderDoc = projectStore.documents.find(d => d.doc_type === 'tender')
  const bidDoc = projectStore.documents.find(d => d.doc_type === 'bid')
  return tenderDoc?.status === 'parsed' && bidDoc?.status === 'parsed'
})

onMounted(async () => {
  await projectStore.fetchReviewTasks()
  // 默认选中最新任务
  if (completedTasks.value.length > 0) {
    selectedTaskId.value = completedTasks.value[0].id
    await loadHistoricalSteps()
  }
})

watch(selectedTaskId, async (newTaskId) => {
  if (newTaskId && !projectStore.reviewLoading) {
    await loadHistoricalSteps()
  }
})

async function loadHistoricalSteps() {
  if (!selectedTaskId.value || !props.projectId) return
  try {
    isHistoricalMode.value = true
    const steps = await reviewApi.getSteps(props.projectId, selectedTaskId.value)
    historicalSteps.value = steps.map(s => ({
      step_number: s.step_number,
      step_type: s.step_type,
      tool_name: s.tool_name,
      content: s.content,
      timestamp: s.timestamp,
      tool_args: s.tool_args,
      tool_result: s.tool_result,
    }))
  } catch {
    ElMessage.error('加载历史时间线失败')
  }
}

async function startReview() {
  try {
    // 重置选择
    isHistoricalMode.value = false
    historicalSteps.value = []
    await projectStore.startReview()
    ElMessage.info('审查已启动，正在连接事件流...')
    if (projectStore.currentTask?.id) {
      selectedTaskId.value = projectStore.currentTask.id
      timelineRef.value?.connect(projectStore.currentTask.id)
    }
  } catch {
    ElMessage.error('启动审查失败')
  }
}

function handleTaskComplete(taskId: string) {
  emit('task-complete', taskId)
  // 刷新任务列表
  projectStore.fetchReviewTasks()
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'N/A'
  return new Date(dateStr).toLocaleString('zh-CN')
}
</script>

<template>
  <div class="timeline-area">
    <!-- Task Selector -->
    <div v-if="completedTasks.length > 0" class="task-selector">
      <label>选择任务查看时间线:</label>
      <select v-model="selectedTaskId">
        <option v-for="task in completedTasks" :key="task.id" :value="task.id">
          {{ formatDate(task.completed_at) }} - {{ task.status }}
        </option>
      </select>
    </div>

    <!-- Review Controls -->
    <div class="review-controls">
      <button
        class="primary-btn"
        :disabled="!canStartReview || projectStore.reviewLoading"
        @click="startReview"
      >
        {{ projectStore.reviewLoading ? '启动中...' : '开始审查' }}
      </button>

      <div v-if="projectStore.currentTask" class="task-status">
        <span :class="['status', projectStore.currentTask.status]">
          {{ projectStore.currentTask.status }}
        </span>
        <p v-if="projectStore.currentTask.error_message" class="error-msg">
          {{ projectStore.currentTask.error_message }}
        </p>
      </div>
    </div>

    <!-- Timeline -->
    <ReviewTimeline
      v-if="selectedTaskId"
      ref="timelineRef"
      :task-id="selectedTaskId"
      :initial-steps="historicalSteps"
      :historical-mode="isHistoricalMode"
      @complete="handleTaskComplete"
    />
  </div>
</template>

<style scoped>
.timeline-area {
  margin-top: 1rem;
}

.task-selector {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
  padding: 0.75rem;
  background: #f5f5f5;
  border-radius: 8px;
}

.task-selector label {
  font-weight: 500;
  color: #333;
}

.task-selector select {
  padding: 0.5rem;
  border: 1px solid #ddd;
  border-radius: 4px;
  min-width: 200px;
}

.review-controls {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}

.primary-btn {
  padding: 0.75rem 1.5rem;
  background: #6366f1;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
  transition: background-color 0.2s ease;
}

.primary-btn:hover {
  background: #4f46e5;
}

.primary-btn:disabled {
  background: #d1d5db;
  cursor: not-allowed;
}

.task-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.status {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.6rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 600;
}

.status.completed {
  background: #dcfce7;
  color: #166534;
}

.status.running,
.status.pending {
  background: #fef9c3;
  color: #854d0e;
}

.status.failed {
  background: #fee2e2;
  color: #991b1b;
}

.error-msg {
  color: #dc2626;
  font-size: 0.85rem;
  margin: 0;
}
</style>
```

- [ ] **Step 2: Commit TimelineArea.vue**

```bash
git add frontend/src/components/TimelineArea.vue
git commit -m "feat(frontend): add TimelineArea component with task selector"
```

---

## Task 3: Update ProjectView.vue

**Files:**
- Modify: `frontend/src/views/ProjectView.vue:1-213` (script section)
- Modify: `frontend/src/views/ProjectView.vue:215-395` (template - Review Section)
- Modify: `frontend/src/views/ProjectView.vue:396-451` (template - Results Section)

### Steps:

- [ ] **Step 1: Update imports in ProjectView.vue**

Change:
```typescript
import ReviewTimeline from '@/components/ReviewTimeline.vue'
```

To:
```typescript
import ReviewResultsArea from '@/components/ReviewResultsArea.vue'
import TimelineArea from '@/components/TimelineArea.vue'
```

Remove unused imports and refs:
- Remove: `timelineRef` ref
- Remove: `showHistoricalTimeline` ref
- Remove: `historicalSteps` ref
- Remove: `TimelineStep` interface
- Remove: `selectedHistoryTaskId` ref
- Remove: `originalTaskId` ref
- Remove: `originalReviewResults` ref
- Remove: `completedTasks` computed
- Remove: `loadHistoricalTimeline` function
- Remove: `clearHistoricalTimeline` function
- Remove: `handleRerunReview` function
- Simplify `startReview` to just call `timelineAreaRef.value?.startReview()`

Add new ref:
```typescript
const timelineAreaRef = ref<{ startReview: () => Promise<void> } | null>(null)
```

- [ ] **Step 2: Update onMounted**

Change from:
```typescript
onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  await projectStore.fetchReviewTasks()

  // 如果 currentTask 存在（已完成的任务），自动加载时间线
  if (projectStore.currentTask?.id && projectStore.currentTask.status === 'completed') {
    selectedHistoryTaskId.value = projectStore.currentTask.id
    await loadHistoricalTimeline()
  }
})
```

To:
```typescript
onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  // TimelineArea 组件内部会获取任务列表
})
```

- [ ] **Step 3: Replace Review Section template**

Change from lines 338-394:
```html
<!-- Review Section -->
<section class="section">
  <h2>审查</h2>

  <!-- Review History Selector -->
  <div v-if="completedTasks.length > 0" class="history-selector">
    <label>查看历史记录:</label>
    <select v-model="selectedHistoryTaskId">
      <option value="">-- 选择历史任务 --</option>
      <option v-for="task in completedTasks" :key="task.id" :value="task.id">
        {{ formatDate(task.completed_at) }} - {{ task.status }}
      </option>
    </select>
    <button v-if="selectedHistoryTaskId" @click="clearHistoricalTimeline" class="secondary-btn">
      关闭历史
    </button>
  </div>

  <div v-if="showHistoricalTimeline" class="rerun-section">
    <button @click="handleRerunReview" class="primary-btn">
      重新审查
    </button>
  </div>

  <div class="review-controls">
    <button
      v-if="!showHistoricalTimeline && (!projectStore.currentTask || projectStore.currentTask.status === 'completed' || projectStore.currentTask.status === 'failed')"
      class="primary-btn"
      :disabled="!canStartReview || projectStore.reviewLoading"
      @click="startReview"
    >
      {{ projectStore.reviewLoading ? '启动中...' : '开始审查' }}
    </button>

    <div v-if="projectStore.currentTask" class="task-status">
      <span :class="['status', getStatusClass(projectStore.currentTask.status)]">
        {{ projectStore.currentTask.status }}
      </span>
      <p v-if="projectStore.currentTask.error_message" class="error-msg">
        {{ projectStore.currentTask.error_message }}
      </p>
    </div>
  </div>

  <p v-if="!canStartReview && !tenderDoc && !bidDoc" class="hint">
    请上传招标书和应标书以开始审查。
  </p>

  <!-- Agent Timeline (Live or Historical) -->
  <ReviewTimeline
    v-if="projectStore.currentTask"
    ref="timelineRef"
    :task-id="projectStore.currentTask.id"
    :initial-steps="showHistoricalTimeline ? historicalSteps : []"
    :historical-mode="showHistoricalTimeline"
  />
</section>
```

To:
```html
<!-- Timeline Section -->
<section class="section">
  <h2>时间线</h2>
  <TimelineArea
    ref="timelineAreaRef"
    :project-id="projectId"
    @task-complete="handleTaskComplete"
  />
</section>
```

- [ ] **Step 4: Replace Results Section template**

Change from lines 396-451:
```html
<!-- Results Section -->
<section v-if="projectStore.reviewResults" class="section">
  <h2>审查结果</h2>
  <!-- ... summary and findings list ... -->
</section>
```

To:
```html
<!-- Results Section -->
<section class="section">
  <h2>审查结果</h2>
  <ReviewResultsArea />
</section>
```

- [ ] **Step 5: Add handleTaskComplete function**

Add new function:
```typescript
async function handleTaskComplete(taskId: string) {
  // 任务完成时刷新审查结果
  await projectStore.fetchReviewResults()
  // 刷新任务列表
  await projectStore.fetchReviewTasks()
}
```

- [ ] **Step 6: Update project store to add fetchReviewResults**

Check if `projectStore` has `fetchReviewResults` method. If not, add it:

In `frontend/src/stores/project.ts`, add:
```typescript
async fetchReviewResults() {
  const results = await reviewApi.getResults(this.currentProject!.id)
  this.reviewResults = results
}
```

- [ ] **Step 7: Remove unused functions from ProjectView.vue**

Remove:
- `loadHistoricalTimeline`
- `clearHistoricalTimeline`
- `handleRerunReview`
- `getStatusClass` (if only used by removed code)
- `formatDate` (if only used by removed code)

- [ ] **Step 8: Commit changes**

```bash
git add frontend/src/views/ProjectView.vue frontend/src/stores/project.ts
git commit -m "refactor(frontend): separate review results and timeline into independent components"
```

---

## Task 4: Verify and Test

### Steps:

- [ ] **Step 1: Type check**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: No type errors

- [ ] **Step 2: Build check**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Test in browser manually**

1. Open project detail page
2. Verify: Review results show immediately (project-level)
3. Verify: Timeline shows latest task by default
4. Verify: Selecting historical task changes timeline but not review results
5. Verify: Start new review shows live timeline
6. Verify: After review completes, review results update automatically

---

## Spec Coverage Checklist

- [x] Review results are project-level (always show latest)
- [x] Timeline is task-level (changes with historical task selection)
- [x] Task selector for historical tasks
- [x] Start review triggers new task with live timeline
- [x] Task completion updates review results automatically
- [x] Clear component separation (ReviewResultsArea, TimelineArea)

## Self-Review

- No placeholders (TBD, TODO)
- All file paths are exact
- All code is complete and copy-paste ready
- Type consistency verified across tasks
