<script setup lang="ts">
import { ref, computed } from 'vue'
import type { TodoItem } from '@/types/review'
import SubAgentTimeline from '@/components/execution/SubAgentTimeline.vue'

const props = defineProps<{
  todo: TodoItem
  agentIndex: number
  steps?: Array<{
    step_number: number
    step_type: string
    content: string
    timestamp: Date
    tool_args?: { tool_calls?: Array<{ name: string; arguments: Record<string, any> }> }
    tool_result?: { tool_results?: Array<{ name: string; result: any }> }
  }>
}>()

const isOpen = ref(false)
const showTimeline = ref(false)

const cardClass = computed(() => {
  switch (props.todo.status) {
    case 'completed': return 'ac-done'
    case 'running': return 'ac-active'
    default: return 'ac-wait'
  }
})

const progress = computed(() => {
  if (!props.todo.check_items?.length) return 0
  const completed = props.todo.check_items.filter(c => c.status === 'completed').length
  return Math.round((completed / props.todo.check_items.length) * 100)
})

const agentTag = computed(() => `A${props.agentIndex}`)

const statusText = computed(() => {
  switch (props.todo.status) {
    case 'completed': return '完成'
    case 'running': return '执行中'
    case 'failed': return '失败'
    default: return '等待调度'
  }
})

const criticalFindings = computed(() =>
  (props.todo.result?.findings ?? []).filter(f => f.severity === 'critical')
)

const majorFindings = computed(() =>
  (props.todo.result?.findings ?? []).filter(f => f.severity === 'major')
)

const passedCount = computed(() =>
  (props.todo.result?.findings ?? []).filter(f => f.is_compliant).length
)

function toggle() {
  isOpen.value = !isOpen.value
}

function toggleTimeline() {
  showTimeline.value = !showTimeline.value
}
</script>

<template>
  <div :class="['agent-card', cardClass, { open: isOpen }]" @click="toggle">
    <div class="agent-card-head">
      <div class="ac-avt">{{ agentTag }}</div>
      <div class="ac-info">
        <div class="ac-title">{{ todo.rule_doc_name.replace('.md', '') }}</div>
        <div class="ac-sub">{{ todo.rule_doc_name }} · {{ todo.check_items?.length || 0 }} 个检查项</div>
      </div>
      <div class="ac-right">
        <div class="pbar-outer">
          <div class="pbar-inner" :style="{ width: `${progress}%` }"></div>
        </div>
        <span :class="['chip', `chip-${todo.status === 'completed' ? 'done' : todo.status === 'running' ? 'run' : todo.status === 'failed' ? 'fail' : 'wait'}`]">
          {{ statusText }}
        </span>
        <span class="chevron">›</span>
      </div>
    </div>

    <div class="agent-card-body">
      <div class="dep-section">
        <div class="dep-sec-label">检查项执行链</div>
        <div class="dep-chain">
          <template v-for="(item, idx) in todo.check_items" :key="item.id">
            <div class="dep-node">
              <span :class="['dep-pill', `dp-${item.status || 'wait'}`]">
                <span class="dp-dot"></span>
                {{ item.title }}
              </span>
            </div>
            <span v-if="idx < todo.check_items.length - 1" class="dep-arr">→</span>
          </template>
        </div>
      </div>

      <div v-if="todo.status === 'running'" class="run-log">
        <span class="log-cursor"></span>
        正在执行检查项...
      </div>

      <div v-if="props.todo.result?.findings?.length" class="findings">
        <span v-for="f in criticalFindings" :key="f.requirement_key" class="finding-tag ft-crit">
          <svg width="8" height="8" viewBox="0 0 8 8"><circle cx="4" cy="4" r="3" fill="var(--red)" opacity=".3"/><circle cx="4" cy="4" r="1.5" fill="var(--red)"/></svg>
          严重: {{ f.requirement_content?.slice(0, 20) }}...
        </span>
        <span v-for="f in majorFindings" :key="f.requirement_key" class="finding-tag ft-major">
          <svg width="8" height="8" viewBox="0 0 8 8"><circle cx="4" cy="4" r="3" fill="var(--amber)" opacity=".3"/><circle cx="4" cy="4" r="1.5" fill="var(--amber)"/></svg>
          一般: {{ f.requirement_content?.slice(0, 20) }}...
        </span>
        <span v-if="passedCount" class="finding-tag ft-pass">
          <svg width="8" height="8" viewBox="0 0 8 8"><circle cx="4" cy="4" r="3" fill="var(--green)" opacity=".3"/><circle cx="4" cy="4" r="1.5" fill="var(--green)"/></svg>
          通过: {{ passedCount }} 项
        </span>
      </div>

      <div v-if="todo.status === 'completed'" class="timeline-toggle">
        <button class="timeline-btn" @click.stop="toggleTimeline">
          {{ showTimeline ? '收起时间线' : '查看时间线' }}
          <span class="btn-icon">{{ showTimeline ? '↑' : '↓' }}</span>
        </button>
      </div>

      <SubAgentTimeline
        v-if="showTimeline"
        :steps="steps || []"
      />
    </div>
  </div>
</template>

<style scoped>
.agent-card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  overflow: hidden;
}
.agent-card.ac-active { border-color: var(--purple-dim); }
.agent-card.ac-done { border-color: var(--green-dim); }

.agent-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--bg2);
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  user-select: none;
}
.agent-card.ac-active .agent-card-head { background: var(--purple-bg); }
.agent-card.ac-done .agent-card-head { background: var(--green-bg); }

.ac-avt {
  width: 26px; height: 26px;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 600;
  flex-shrink: 0;
}
.ac-done .ac-avt { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-dim); }
.ac-active .ac-avt { background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple-dim); }
.ac-wait .ac-avt { background: var(--bg3); color: var(--muted); border: 1px solid var(--line2); }

.ac-info { flex: 1; min-width: 0; }
.ac-title { font-size: 12px; font-weight: 500; color: var(--bright); }
.ac-sub { font-size: 11px; color: var(--muted); margin-top: 1px; }

.ac-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.pbar-outer { width: 72px; height: 3px; background: var(--bg4); border-radius: 2px; overflow: hidden; }
.pbar-inner { height: 100%; border-radius: 2px; transition: width 0.5s ease; }
.ac-done .pbar-inner { background: var(--green); }
.ac-active .pbar-inner { background: var(--purple); }
.ac-wait .pbar-inner { background: var(--dim); }

.chip { font-size: 10px; font-weight: 500; padding: 2px 7px; border-radius: 3px; border: 1px solid; }
.chip-done { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
.chip-run { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
.chip-fail { background: var(--red-bg); border-color: var(--red-dim); color: var(--red); }

.chevron { font-size: 10px; color: var(--dim); transition: transform 0.2s; }
.agent-card.open .chevron { transform: rotate(90deg); }

.agent-card-body {
  padding: 12px 14px;
  display: none;
  flex-direction: column;
  gap: 12px;
}
.agent-card.open .agent-card-body { display: flex; }

.dep-sec-label {
  font-size: 10px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
}

.dep-chain {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 3px;
}
.dep-node { display: flex; align-items: center; gap: 3px; }
.dep-pill {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: var(--r);
  border: 1px solid;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}
.dp-done { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }
.dp-run { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }
.dp-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }
.dp-fail { background: var(--red-bg); border-color: var(--red-dim); color: var(--red); }
.dp-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dp-done .dp-dot { background: var(--green); }
.dp-run .dp-dot { background: var(--purple); animation: blink 1s infinite; }
.dp-wait .dp-dot { background: var(--muted); }
.dp-fail .dp-dot { background: var(--red); }
.dep-arr { color: var(--dim); font-size: 10px; padding: 0 1px; }

.run-log {
  font-size: 11px;
  color: var(--muted);
  background: var(--bg3);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 8px 10px;
  font-style: italic;
  display: flex;
  align-items: center;
  gap: 6px;
}
.log-cursor {
  width: 6px; height: 12px;
  background: var(--purple);
  display: inline-block;
  border-radius: 1px;
  animation: blink 0.9s step-end infinite;
}

.findings {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
.finding-tag {
  font-size: 10px;
  font-weight: 500;
  padding: 3px 8px;
  border-radius: 3px;
  border: 1px solid;
  display: flex;
  align-items: center;
  gap: 4px;
}
.ft-crit { background: var(--red-bg); border-color: var(--red-dim); color: var(--red); }
.ft-major { background: var(--amber-bg); border-color: var(--amber-dim); color: var(--amber); }
.ft-pass { background: var(--green-bg); border-color: var(--green-dim); color: var(--green); }

.timeline-toggle {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.timeline-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: var(--bg3);
  border: 1px solid var(--line2);
  border-radius: var(--r);
  color: var(--sub);
  font-size: 11px;
  cursor: pointer;
}

.timeline-btn:hover {
  background: var(--bg4);
  color: var(--text);
}

.btn-icon {
  font-size: 10px;
}

@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }
</style>
