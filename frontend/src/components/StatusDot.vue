<script setup lang="ts">
/**
 * 统一状态灯：呼吸表「活着」，颜色表「健康度」（2026-09-11 界面规范，替代时间承诺文案）。
 * - running 蓝·呼吸：正在处理（LLM 生成 / 解析 / 索引 / 写入 Word）
 * - waiting 黄·慢呼吸：等待依赖或排队（索引等解析、撰写排队）
 * - done   绿·常亮：成功完成
 * - error  红·进入闪一次后常亮：失败（须配错误摘要与重试入口）
 * - todo   橙·常亮：已完成待用户动作（章节已生成待写入）
 * - idle   灰·常亮：未开始 / 已取消
 * 有真实计数时由调用方把计数写进默认插槽；禁止展示时间预估。
 */
defineProps<{
  tone: 'running' | 'waiting' | 'done' | 'error' | 'todo' | 'idle';
}>();
</script>

<template>
  <span class="status-line">
    <span class="status-dot" :class="`status-dot--${tone}`" role="img" :aria-label="tone" />
    <span v-if="$slots.default" class="status-label"><slot /></span>
  </span>
</template>

<style scoped>
.status-line{display:inline-flex;align-items:center;gap:7px;min-width:0;line-height:1.5}
.status-dot{flex:none;width:9px;height:9px;border-radius:50%}
.status-dot--running{--dot-glow:rgba(47,111,221,.4);background:#2f6fdd;animation:dot-breath 1.6s ease-in-out infinite}
.status-dot--waiting{--dot-glow:rgba(250,173,20,.4);background:#faad14;animation:dot-breath 2.8s ease-in-out infinite}
.status-dot--done{background:#52c41a}
.status-dot--error{background:#ff4d4f;animation:dot-flash .6s ease-out 1}
.status-dot--todo{background:#fa8c16}
.status-dot--idle{background:#c0c4cc}
.status-label{min-width:0;word-break:break-word}
@keyframes dot-breath{
  0%,100%{box-shadow:0 0 0 0 var(--dot-glow,rgba(47,111,221,.4));opacity:.65}
  50%{box-shadow:0 0 0 6px rgba(0,0,0,0);opacity:1}
}
@keyframes dot-flash{
  0%{box-shadow:0 0 0 0 rgba(255,77,79,.55)}
  100%{box-shadow:0 0 0 8px rgba(255,77,79,0)}
}
@media (prefers-reduced-motion:reduce){
  .status-dot{animation:none !important;opacity:1}
}
</style>
