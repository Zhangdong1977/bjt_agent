<script setup lang="ts">
import { computed } from 'vue'
import TodoListCard from '@/components/TodoListCard.vue'
import SubAgentCard from '@/components/SubAgentCard.vue'
import type { TodoItem } from '@/types/review'

const props = defineProps<{
  phase: 'master' | 'todo' | 'sub_agents' | 'merging' | 'completed'
  todos: TodoItem[]
  masterOutput?: {
    totalDocs: number
    totalItems: number
    ruleDocs: Array<{ name: string; items: number }>
  } | null
}>()

const agentIndexMap = computed(() => {
  const map = new Map<string, number>()
  let idx = 1
  for (const todo of props.todos) {
    map.set(todo.id, idx++)
  }
  return map
})
</script>

<template>
  <div class="left-pane">
    <!-- 主代理输出块 - 解析阶段 -->
    <div v-if="phase !== 'master' || masterOutput" class="phase-block">
      <div class="phase-label">主代理 · 解析阶段</div>
      <div class="output-block">
        <div class="output-header">
          <div class="output-header-icon master-icon">
            <svg viewBox="0 0 11 11" fill="none">
              <circle cx="5.5" cy="5.5" r="4" stroke="var(--purple)" stroke-width="1.2"/>
              <path d="M5.5 3.5v2.5l1.5 1" stroke="var(--purple)" stroke-width="1.1" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="output-title">主代理 — 规则解析</span>
          <span class="chip chip-master">MASTER</span>
        </div>
        <div class="output-body">
          <div v-if="masterOutput" class="output-line">
            <span class="prompt">›</span>
            <span class="cmd">
              <span class="keyword">Scanning</span>
              <span class="path">{{ masterOutput.ruleDocs.length }} rule documents</span>
              <span class="ok">found {{ masterOutput.totalDocs }} docs</span>
            </span>
          </div>
          <div v-if="masterOutput" v-for="doc in masterOutput.ruleDocs" :key="doc.name" class="output-line">
            <span class="prompt">›</span>
            <span class="cmd">
              <span class="keyword">Parsing</span>
              <span class="path">{{ doc.name }}</span>
              <span class="val">→ {{ doc.items }} items</span>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 待办列表 -->
    <div v-if="phase !== 'master'" class="phase-block">
      <div class="phase-label">待办任务列表</div>
      <div class="output-block">
        <div class="output-header">
          <div class="output-header-icon todo-icon">
            <svg viewBox="0 0 11 11" fill="none">
              <path d="M2 3h7M2 5.5h7M2 8h4.5" stroke="var(--amber)" stroke-width="1.2" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="output-title">待办任务列表</span>
          <span class="chip chip-todo">TODO · {{ todos.length }} tasks</span>
        </div>
        <div class="output-body">
          <TodoListCard
            v-for="todo in todos"
            :key="todo.id"
            :todo="todo"
            class="todo-item-wrapper"
          />
        </div>
      </div>
    </div>

    <!-- 子代理卡片组 -->
    <div v-if="phase === 'sub_agents' || phase === 'merging' || phase === 'completed'" class="phase-block">
      <div class="phase-label">子代理并行执行</div>
      <div class="agent-list">
        <SubAgentCard
          v-for="todo in todos"
          :key="todo.id"
          :todo="todo"
          :agent-index="agentIndexMap.get(todo.id) || 1"
        />
      </div>
    </div>

    <!-- 合并块 -->
    <div v-if="phase === 'merging' || phase === 'completed'" class="phase-block">
      <div class="phase-label">合并与质检阶段</div>
      <div class="merge-block">
        <div class="merge-header">
          <div class="output-header-icon merge-icon">
            <svg viewBox="0 0 11 11" fill="none">
              <path d="M2 5.5h7M5.5 2l3.5 3.5-3.5 3.5" stroke="var(--teal)" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>
          <span class="output-title">结果合并与质检</span>
          <span :class="['chip', phase === 'completed' ? 'chip-done' : 'chip-wait']">
            {{ phase === 'completed' ? '完成' : '等待中' }}
          </span>
        </div>
        <div class="merge-steps">
          <div class="merge-step"><div class="m-dot"></div>汇总子代理结果</div>
          <span class="merge-arr">→</span>
          <div class="merge-step"><div class="m-dot"></div>去重与标准化</div>
          <span class="merge-arr">→</span>
          <div class="merge-step"><div class="m-dot"></div>优先级排序</div>
          <span class="merge-arr">→</span>
          <div class="merge-step"><div class="m-dot"></div>异常二次校验</div>
          <span class="merge-arr">→</span>
          <div class="merge-step"><div class="m-dot"></div>生成审查报告</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.left-pane {
  padding: 20px 24px;
  border-right: 1px solid var(--line);
  overflow-y: auto;
  height: 100%;
}

.phase-block {
  margin-bottom: 24px;
}

.phase-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.phase-label::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line);
}

/* 输出块 */
.output-block {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  overflow: hidden;
}

.output-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--bg2);
}

.output-header-icon {
  width: 20px;
  height: 20px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.master-icon {
  background: var(--purple-bg);
  border: 1px solid var(--purple-dim);
}

.todo-icon {
  background: var(--amber-bg);
  border: 1px solid var(--amber-dim);
}

.merge-icon {
  background: var(--teal-bg);
  border: 1px solid var(--teal-dim);
}

.output-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--bright);
  flex: 1;
}

.output-body {
  padding: 12px 14px;
}

.output-line {
  font-size: 12px;
  color: var(--sub);
  line-height: 1.7;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.prompt {
  color: var(--dim);
  flex-shrink: 0;
}

.cmd {
  color: var(--text);
}

.keyword {
  color: var(--purple);
}

.path {
  color: var(--blue);
}

.val {
  color: var(--amber);
}

.ok {
  color: var(--green);
}

.todo-item-wrapper {
  margin-bottom: 4px;
}

/* 代理列表 */
.agent-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 合并块 */
.merge-block {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  padding: 14px;
}

.merge-block.opacity-50 {
  opacity: 0.5;
}

.merge-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 10px;
}

.merge-steps {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.merge-step {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--muted);
}

.m-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--line2);
}

.merge-arr {
  color: var(--dim);
  font-size: 11px;
}

/* Chip 样式 */
.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}

.chip-master {
  background: var(--purple-bg);
  border-color: var(--purple-dim);
  color: var(--purple);
}

.chip-todo {
  background: var(--amber-bg);
  border-color: var(--amber-dim);
  color: var(--amber);
}

.chip-done {
  background: var(--green-bg);
  border-color: var(--green-dim);
  color: var(--green);
}

.chip-wait {
  background: var(--bg3);
  border-color: var(--line2);
  color: var(--muted);
}
</style>