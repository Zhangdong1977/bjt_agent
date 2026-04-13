<script setup lang="ts">
interface TodoItem {
  id: string
  name: string
  ruleFile: string
  checkItemsCount: number
  depsType: 'sequential' | 'branching'
  status: 'done' | 'running' | 'wait'
  agentId: string
}

defineProps<{
  items: TodoItem[]
}>()

function getStatusClass(status: string) {
  return {
    'td-done': status === 'done',
    'td-run': status === 'running',
    'td-wait': status === 'wait'
  }
}

function getAgentTagClass(status: string) {
  return {
    'tag-done': status === 'done',
    'tag-run': status === 'running',
    'tag-wait': status === 'wait'
  }
}
</script>

<template>
  <div class="todo-list">
    <div
      v-for="item in items"
      :key="item.id"
      :class="['todo-item', getStatusClass(item.status)]"
    >
      <div class="todo-check">
        <span v-if="item.status === 'done'">✓</span>
        <span v-else-if="item.status === 'running'" class="todo-spin"></span>
      </div>
      <div class="todo-body">
        <div class="todo-name">{{ item.name }}</div>
        <div class="todo-meta">
          <span class="file">{{ item.ruleFile }}</span>
          <span class="sep">·</span>
          <span>{{ item.checkItemsCount }} 个检查项 · {{ item.depsType === 'sequential' ? '顺序依赖' : '分支依赖' }}</span>
        </div>
      </div>
      <div class="todo-right">
        <span :class="['agent-tag', getAgentTagClass(item.status)]">{{ item.agentId }}</span>
        <span :class="['chip', item.status === 'done' ? 'chip-done' : item.status === 'running' ? 'chip-run' : 'chip-wait']">
          {{ item.status === 'done' ? '完成' : item.status === 'running' ? '执行中' : '等待' }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.todo-list { display: flex; flex-direction: column; gap: 3px; }

.todo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border-radius: var(--r);
  transition: background 0.15s;
}
.todo-item:hover { background: var(--bg3); }

.todo-check {
  width: 16px; height: 16px;
  border-radius: 3px;
  border: 1px solid;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  font-size: 9px;
}
.todo-item.td-done .todo-check { border-color: var(--green-dim); background: var(--green-bg); color: var(--green); }
.todo-item.td-run .todo-check { border-color: var(--purple-dim); background: var(--purple-bg); color: var(--purple); }
.todo-item.td-wait .todo-check { border-color: var(--line2); background: var(--bg2); color: transparent; }

.todo-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.todo-name { font-size: 12px; font-weight: 500; color: var(--bright); }
.todo-item.td-done .todo-name { color: var(--sub); text-decoration: line-through; text-decoration-color: var(--dim); }
.todo-meta { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
.todo-meta .file { color: var(--blue); opacity: 0.7; }
.todo-meta .sep { color: var(--dim); }

.todo-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.agent-tag {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 3px;
  letter-spacing: 0.03em;
}
.tag-done { background: var(--green-bg); border: 1px solid var(--green-dim); color: var(--green); }
.tag-run { background: var(--purple-bg); border: 1px solid var(--purple-dim); color: var(--purple); }
.tag-wait { background: var(--bg3); border: 1px solid var(--line2); color: var(--muted); }

.todo-spin {
  width: 10px; height: 10px;
  border-radius: 50%;
  border: 1.5px solid var(--purple-dim);
  border-top-color: var(--purple);
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}
.chip-done { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
.chip-run { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
</style>
