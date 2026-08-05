<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { documentsApi } from '@/api/client'
import type { DocumentArtifactsResponse } from '@/types'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const error = ref('')
const data = ref<DocumentArtifactsResponse | null>(null)

const projectId = computed(() => String(route.params.id || ''))
const documentId = computed(() => String(route.params.documentId || ''))

const statusText = computed(() => {
  const status = data.value?.coverage?.status
  if (status === 'complete') return '完整'
  if (status === 'partial') return '部分覆盖'
  if (status === 'insufficient') return '覆盖不足'
  return '暂无制品'
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = projectId.value
      ? await documentsApi.getArtifacts(projectId.value, documentId.value, true, 500)
      : await documentsApi.getDraftArtifacts(documentId.value, true, 500)
  } catch (err) {
    console.error('Failed to load document artifacts', err)
    error.value = '加载解析诊断信息失败，请确认文档权限或稍后重试'
  } finally {
    loading.value = false
  }
}

function goBack() {
  router.back()
}

onMounted(load)
</script>

<template>
  <div class="artifact-page">
    <div class="page-header">
      <button class="back-btn" @click="goBack">返回</button>
      <div>
        <h1>文档解析诊断</h1>
        <p class="muted">S2-0 制品清单、证据块和覆盖度，仅供内部复核</p>
      </div>
      <button class="refresh-btn" :disabled="loading" @click="load">刷新</button>
    </div>

    <div v-if="loading" class="state">正在加载解析制品……</div>
    <div v-else-if="error" class="state error">{{ error }}</div>
    <template v-else-if="data">
      <section class="summary-grid">
        <div class="summary-card">
          <span class="label">覆盖状态</span>
          <strong :class="['status', data.coverage?.status || 'unknown']">{{ statusText }}</strong>
        </div>
        <div class="summary-card">
          <span class="label">证据块</span>
          <strong>{{ data.block_count }}</strong>
        </div>
        <div class="summary-card">
          <span class="label">文本覆盖</span>
          <strong>{{ Math.round((data.coverage?.text_ratio || 0) * 100) }}%</strong>
        </div>
        <div class="summary-card">
          <span class="label">图片哈希</span>
          <strong>{{ Math.round((data.coverage?.image_hash_ratio || 0) * 100) }}%</strong>
        </div>
      </section>

      <section v-if="data.coverage?.warnings?.length" class="panel warning-panel">
        <h2>覆盖警告</h2>
        <ul>
          <li v-for="warning in data.coverage.warnings" :key="warning">{{ warning }}</li>
        </ul>
      </section>

      <section v-if="data.manifest" class="panel">
        <h2>解析制品</h2>
        <div class="meta-grid">
          <span>Schema</span><strong>{{ data.manifest.schema_version }}</strong>
          <span>解析器</span><strong>{{ data.manifest.parser_name }} / {{ data.manifest.parser_version }}</strong>
          <span>源文件</span><strong>{{ data.manifest.source.name }}</strong>
          <span>源文件 SHA-256</span><strong class="mono">{{ data.manifest.source.sha256 || '—' }}</strong>
        </div>
      </section>

      <section class="panel">
        <h2>证据块明细（{{ data.blocks.length }} / {{ data.block_count }}）</h2>
        <div v-if="!data.blocks.length" class="muted">没有可展示的证据块。</div>
        <div v-else class="block-list">
          <article v-for="block in data.blocks" :key="block.block_id" class="block-item">
            <div class="block-head">
              <span class="badge">{{ block.content_type }}</span>
              <span class="muted">{{ block.section_path.join(' / ') || '正文' }} · 第 {{ block.start_line || '—' }} 行</span>
            </div>
            <p>{{ block.raw_text }}</p>
            <div v-if="block.numbers.length || block.models.length" class="block-meta">
              <span v-if="block.numbers.length">数字：{{ block.numbers.join('、') }}</span>
              <span v-if="block.models.length">型号：{{ block.models.join('、') }}</span>
            </div>
          </article>
        </div>
        <p v-if="data.truncated" class="muted">明细已按上限截断，完整块数仍以 manifest 为准。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.artifact-page { max-width: 1200px; margin: 0 auto; padding: 24px; }
.page-header { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; }
.page-header h1 { margin: 0; color: var(--text); }
.page-header p { margin: 4px 0 0; }
.back-btn, .refresh-btn { border: 1px solid var(--line); background: var(--bg1); border-radius: 6px; padding: 7px 14px; cursor: pointer; }
.refresh-btn { margin-left: auto; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.summary-card, .panel { background: var(--bg1); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
.summary-card { display: flex; flex-direction: column; gap: 8px; }
.summary-card strong { font-size: 1.25rem; }
.label, .muted { color: var(--muted); }
.status.complete { color: #16803c; }
.status.partial { color: #ad6800; }
.status.insufficient, .error { color: #c5221f; }
.panel { margin-bottom: 16px; }
.panel h2 { margin: 0 0 12px; font-size: 1rem; }
.warning-panel { border-color: #f0c36d; background: #fffaf0; }
.warning-panel ul { margin: 0; padding-left: 20px; }
.meta-grid { display: grid; grid-template-columns: 150px 1fr; gap: 8px 16px; }
.mono { word-break: break-all; font-family: monospace; }
.block-list { display: grid; gap: 10px; max-height: 70vh; overflow-y: auto; }
.block-item { border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
.block-head { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.badge { background: var(--bg3); border-radius: 4px; padding: 2px 7px; font-size: 12px; }
.block-item p { margin: 0; white-space: pre-wrap; word-break: break-word; }
.block-meta { margin-top: 8px; display: flex; gap: 12px; color: var(--muted); font-size: 12px; }
.state { padding: 40px; text-align: center; }
@media (max-width: 800px) { .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .meta-grid { grid-template-columns: 1fr; } }
</style>
