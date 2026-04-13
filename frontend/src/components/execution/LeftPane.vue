<script setup lang="ts">
import { computed } from 'vue'
import TodoList from './TodoList.vue'
import MasterOutputBlock from './MasterOutputBlock.vue'
import SubAgentTimeline from './SubAgentTimeline.vue'
import MergeBlock from './MergeBlock.vue'

interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface ToolResult {
  name: string
  result: any
}

interface TimelineStep {
  step_number: number
  step_type: string
  content: string
  timestamp: Date
  tool_args?: {
    tool_calls?: ToolCall[]
  }
  tool_result?: {
    tool_results?: ToolResult[]
  }
}

const props = defineProps<{
  phase: 'pending' | 'running' | 'completed' | 'failed'
  steps: TimelineStep[]
  errorMessage?: string | null
}>()

// 模拟数据 - 实际应从 steps 解析
const masterSteps = computed(() =>
  props.steps.filter(s => s.step_type === 'master')
)

const todoItems = [
  { id: '1', name: '检查投标方资质合规性', ruleFile: 'rule_001_资质要求.md', checkItemsCount: 5, depsType: 'sequential' as const, status: 'done' as const, agentId: 'A1' },
  { id: '2', name: '核验技术方案规格参数', ruleFile: 'rule_002_技术规格.md', checkItemsCount: 8, depsType: 'branching' as const, status: 'done' as const, agentId: 'A2' },
  { id: '3', name: '审核商务条款与合同约定', ruleFile: 'rule_003_商务条款.md', checkItemsCount: 4, depsType: 'sequential' as const, status: 'running' as const, agentId: 'A3' },
  { id: '4', name: '验证环保合规与节能指标', ruleFile: 'rule_004_环保要求.md', checkItemsCount: 3, depsType: 'sequential' as const, status: 'wait' as const, agentId: 'A4' }
]

const subAgents = computed(() => [
  {
    agentId: 'A1',
    title: '检查投标方资质合规性',
    ruleFile: 'rule_001_资质要求.md · 5 个检查项',
    checkItems: [
      { name: '营业执照', status: 'done' as const },
      { name: '资质等级', status: 'done' as const },
      { name: '信用评级', status: 'done' as const },
      { name: '业绩证明', status: 'done' as const },
      { name: '人员配置', status: 'fail' as const }
    ],
    status: 'done' as const,
    findings: [
      { type: 'crit' as const, text: '严重: 项目经理资质证书缺失' },
      { type: 'major' as const, text: '一般: 业绩年限不足 1 项' },
      { type: 'pass' as const, text: '通过: 3 项' }
    ]
  },
  {
    agentId: 'A2',
    title: '核验技术方案规格参数',
    ruleFile: 'rule_002_技术规格.md · 8 个检查项',
    checkItems: [
      { name: '基础架构', status: 'done' as const },
      { name: '网络带宽', status: 'done' as const },
      { name: '安全等级', status: 'done' as const },
      { name: '灾备方案', status: 'done' as const },
      { name: '响应时间', status: 'done' as const },
      { name: '+3项', status: 'done' as const }
    ],
    status: 'done' as const,
    findings: [
      { type: 'major' as const, text: '一般: 灾备恢复时间超标' },
      { type: 'major' as const, text: '一般: 带宽冗余描述不清' },
      { type: 'major' as const, text: '一般: 数据加密方案缺细节' },
      { type: 'pass' as const, text: '通过: 5 项' }
    ]
  },
  {
    agentId: 'A3',
    title: '审核商务条款与合同约定',
    ruleFile: 'rule_003_商务条款.md · 4 个检查项',
    checkItems: [
      { name: '合同条款', status: 'done' as const },
      { name: '付款方式', status: 'done' as const },
      { name: '投标有效期', status: 'run' as const },
      { name: '保证金条款', status: 'wait' as const }
    ],
    status: 'running' as const,
    runningLog: '正在比对"投标有效期"条款 — 检查是否满足 ≥ 90 天要求...',
    findings: [
      { type: 'pass' as const, text: '通过: 合同条款 · 付款方式' }
    ]
  },
  {
    agentId: 'A4',
    title: '验证环保合规与节能指标',
    ruleFile: 'rule_004_环保要求.md · 3 个检查项',
    checkItems: [
      { name: '环保认证', status: 'wait' as const },
      { name: '碳排放指标', status: 'wait' as const },
      { name: '废料处理方案', status: 'wait' as const }
    ],
    status: 'wait' as const,
    findings: []
  }
])
</script>

<template>
  <div class="left-pane">
    <!-- 错误信息块 -->
    <div v-if="errorMessage" class="phase-block">
      <div class="phase-label">错误</div>
      <div class="error-block">
        <div class="error-icon">⚠️</div>
        <div class="error-content">
          <span class="error-label">审查失败:</span>
          <span class="error-message">{{ errorMessage }}</span>
        </div>
      </div>
    </div>

    <!-- 待办任务列表 -->
    <div v-if="phase === 'running' || phase === 'completed'" class="phase-block">
      <div class="phase-label">待办任务列表</div>
      <div class="output-block">
        <div class="output-header">
          <div class="output-header-icon" style="background:var(--amber-bg);border:1px solid var(--amber-dim)">
            <svg viewBox="0 0 11 11" fill="none">
              <path d="M2 3h7M2 5.5h7M2 8h4.5" stroke="#f0a429" stroke-width="1.2" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="output-header-title">待办任务列表</span>
          <div class="output-header-meta">
            <span class="chip chip-todo">TODO · {{ todoItems.length }} tasks</span>
          </div>
        </div>
        <div class="output-body">
          <TodoList :items="todoItems" />
        </div>
      </div>
    </div>

    <!-- 主代理输出 -->
    <div v-if="masterSteps.length > 0" class="phase-block">
      <div class="phase-label">主代理 · 解析阶段</div>
      <MasterOutputBlock :steps="masterSteps" />
    </div>

    <!-- 子代理时间线 -->
    <div v-if="phase === 'running' || phase === 'completed'" class="phase-block">
      <SubAgentTimeline :agents="subAgents" />
    </div>

    <!-- 合并阶段 -->
    <div v-if="phase === 'merging' || phase === 'completed'" class="phase-block">
      <div class="phase-label">合并与质检阶段</div>
      <MergeBlock :status="phase === 'completed' ? 'done' : phase === 'merging' ? 'running' : 'wait'" />
    </div>

    <!-- 空状态 -->
    <div v-if="phase === 'pending'" class="phase-block">
      <div class="phase-label">等待开始</div>
      <div class="output-block">
        <div class="output-header">
          <div class="output-header-icon wait-icon">
            <svg viewBox="0 0 11 11" fill="none">
              <circle cx="5.5" cy="5.5" r="4" stroke="var(--muted)" stroke-width="1.2"/>
              <path d="M5.5 4v2l1.5 1" stroke="var(--muted)" stroke-width="1.1" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="output-title">等待智能体启动</span>
          <span class="chip chip-wait">等待</span>
        </div>
        <div class="output-body">
          <div class="wait-status">
            <span>正在等待服务器响应...</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 复用现有样式 */
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
  flex-shrink: 0;
}

.wait-icon {
  background: var(--bg3);
  border: 1px solid var(--line2);
}

.output-title {
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
.chip-todo { background: var(--amber-bg); border-color: var(--amber-dim); color: var(--amber); }
.chip-wait { background: var(--bg3); border-color: var(--line2); color: var(--muted); }

.output-body { padding: 12px 14px; }

.error-block {
  background: var(--red-bg);
  border: 1px solid var(--red-dim);
  border-radius: var(--r2);
  padding: 14px;
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.error-icon { font-size: 20px; }

.error-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.error-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--red);
}

.error-message {
  font-size: 12px;
  color: var(--text);
}

.wait-status {
  font-size: 12px;
  color: var(--muted);
}
</style>
