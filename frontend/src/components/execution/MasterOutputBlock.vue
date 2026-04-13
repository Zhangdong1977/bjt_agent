<script setup lang="ts">
const MAX_CONTENT_LENGTH = 100
const MAX_ARGS_DISPLAY_LENGTH = 50

interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface MasterStep {
  step_number: number
  content: string
  timestamp: Date
  tool_calls?: ToolCall[]
  tool_results?: any[]
}

defineProps<{
  steps: MasterStep[]
}>()

function formatTime(date: Date): string {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  })
}

function formatToolResult(result: any): string {
  if (!result) return ''
  if (result.status === 'success' && result.content) {
    return result.content.slice(0, MAX_CONTENT_LENGTH) + (result.content.length > MAX_CONTENT_LENGTH ? '...' : '')
  }
  if (result.status === 'error') return `失败: ${result.error || 'unknown'}`
  try {
    return JSON.stringify(result).slice(0, MAX_CONTENT_LENGTH)
  } catch {
    return String(result).slice(0, MAX_CONTENT_LENGTH)
  }
}

function formatArgs(args: Record<string, any>): string {
  try {
    return JSON.stringify(args).slice(0, MAX_ARGS_DISPLAY_LENGTH)
  } catch {
    return String(args).slice(0, MAX_ARGS_DISPLAY_LENGTH)
  }
}
</script>

<template>
  <div class="output-block">
    <div class="output-header">
      <div class="output-header-icon" style="background:var(--purple-bg);border:1px solid var(--purple-dim)">
        <svg viewBox="0 0 11 11" fill="none">
          <circle cx="5.5" cy="5.5" r="4" stroke="#a78bfa" stroke-width="1.2"/>
          <path d="M5.5 3.5v2.5l1.5 1" stroke="#a78bfa" stroke-width="1.1" stroke-linecap="round"/>
        </svg>
      </div>
      <span class="output-header-title">主代理 — 规则解析</span>
      <div class="output-header-meta">
        <span class="chip chip-master">MASTER</span>
        <span class="ts" v-if="steps.length > 0">{{ formatTime(new Date(steps[steps.length - 1].timestamp)) }}</span>
      </div>
    </div>
    <div class="output-body">
      <div v-for="step in steps" :key="step.step_number" class="output-line">
        <span class="prompt">›</span>
        <span class="cmd">{{ step.content }}</span>
      </div>
      <div v-if="steps.length > 0 && steps[0].tool_calls?.length" class="tool-call">
        <span class="tool-call-fn">{{ steps[0].tool_calls[0].name }}</span>
        <span class="tool-call-arrow">·</span>
        <span class="tool-call-arg">{{ formatArgs(steps[0].tool_calls[0].arguments) }}...</span>
        <span class="tool-call-arrow">→</span>
        <span class="tool-call-result" v-if="steps[0].tool_results?.[0]">
          {{ formatToolResult(steps[0].tool_results[0].result) }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
  width: 20px; height: 20px;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
}

.output-header-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--bright);
  flex: 1;
}

.output-header-meta { display: flex; align-items: center; gap: 8px; }

.chip {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid;
}
.chip-master { background: var(--purple-bg); border-color: var(--purple-dim); color: var(--purple); }

.ts { font-size: 11px; color: var(--dim); font-style: italic; }

.output-body { padding: 12px 14px; }

.output-line {
  font-size: 12px;
  color: var(--sub);
  line-height: 1.7;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.output-line .prompt { color: var(--dim); flex-shrink: 0; }
.output-line .cmd { color: var(--text); }

.tool-call {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: var(--bg3);
  border: 1px solid var(--line2);
  border-radius: var(--r);
  margin-top: 8px;
  font-size: 11px;
}
.tool-call-fn { color: var(--blue); font-weight: 500; }
.tool-call-arg { color: var(--muted); }
.tool-call-arrow { color: var(--dim); }
.tool-call-result { color: var(--green); }
</style>