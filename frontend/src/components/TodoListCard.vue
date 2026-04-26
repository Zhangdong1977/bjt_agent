<script setup lang="ts">
import { computed } from 'vue'
import type { TodoItem } from '@/types/review'

const props = defineProps<{
  todo: TodoItem
}>()

const emit = defineEmits<{
  (e: 'click', todo: TodoItem): void
}>()

const statusClass = computed(() => {
  switch (props.todo.status) {
    case 'completed': return 'td-done'
    case 'running': return 'td-run'
    case 'failed': return 'td-fail'
    default: return 'td-wait'
  }
})

const statusChipClass = computed(() => {
  switch (props.todo.status) {
    case 'completed': return 'chip-done'
    case 'running': return 'chip-run'
    case 'failed': return 'chip-fail'
    default: return 'chip-wait'
  }
})

const statusText = computed(() => {
  switch (props.todo.status) {
    case 'completed': return '完成'
    case 'running': return '执行中'
    case 'failed': return '失败'
    default: return '等待调度'
  }
})

const agentTag = computed(() => {
  const index = props.todo.rule_doc_name.match(/rule_(\d+)/)?.[1] || '?'
  return `A${index}`
})

const checkItemsCount = computed(() => props.todo.check_items?.length || 0)
</script>

<template>
  <div :class="['todo-item', statusClass]" @click="emit('click', todo)">
    <div class="todo-check">
      <span v-if="todo.status === 'running'" class="todo-spin"></span>
      <span v-else-if="todo.status === 'completed'">✓</span>
    </div>
    <div class="todo-body">
      <div class="todo-name">{{ todo.rule_doc_name.replace('.md', '') }}</div>
      <div class="todo-meta">
        <span class="file">{{ todo.rule_doc_name }}</span>
        <span class="sep">·</span>
        <span>{{ checkItemsCount }} 个检查项</span>
      </div>
    </div>
    <div class="todo-right">
      <span class="agent-tag" :class="`tag-${todo.status === 'completed' ? 'done' : todo.status === 'running' ? 'run' : todo.status === 'failed' ? 'fail' : 'wait'}`">
        {{ agentTag }}
      </span>
      <span :class="['chip', statusChipClass]">{{ statusText }}</span>
    </div>
  </div>
</template>

<style scoped>
.todo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border-radius: var(--r);
  transition: background 0.15s;
  cursor: default;
}
.todo-item:hover { background: var(--bg3); }
.td-done .todo-check { border-color: var(--green-dim); background: var(--green-bg); color: var(--green); }
.td-run .todo-check { border-color: var(--blue-dim); background: var(--blue-bg); color: var(--blue); }
.td-fail .todo-check { border-color: var(--red-dim); background: var(--red-bg); color: var(--red); }
.td-wait .todo-check { border-color: var(--line2); background: var(--bg2); color: transparent; }

.todo-check {
  width: 16px; height: 16px;
  border-radius: 3px;
  border: 1px solid;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  font-size: 9px;
}

.todo-spin {
  width: 10px; height: 10px;
  border-radius: 50%;
  border: 1.5px solid var(--blue-dim);
  border-top-color: var(--blue);
  animation: spin 0.8s linear infinite;
}

.todo-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.todo-name { font-size: 12px; font-weight: 500; color: var(--bright); }
.td-done .todo-name { color: var(--sub); text-decoration: line-through; text-decoration-color: var(--dim); }
.todo-meta { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
.todo-meta .file { color: var(--blue); opacity: 0.7; }
.todo-meta .sep { color: var(--dim); }

.todo-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.agent-tag { font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 3px; }
.tag-done { background: var(--green-bg); border: 1px solid var(--green-dim); color: var(--green); }
.tag-run { background: var(--blue-bg); border: 1px solid var(--blue-dim); color: var(--blue); }
.tag-wait { background: var(--bg3); border: 1px solid var(--line2); color: var(--muted); }
.tag-fail { background: var(--red-bg); border: 1px solid var(--red-dim); color: var(--red); }

.chip { font-size: 10px; font-weight: 500; padding: 2px 7px; border-radius: 3px; border: 1px solid; }
.chip-done { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
.chip-run { background: var(--blue-bg); border-color: var(--blue-dim); color: var(--blue); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
.chip-fail { background: var(--red-bg); border-color: var(--red-dim); color: var(--red); }

@keyframes spin { to { transform: rotate(360deg); } }
</style>
