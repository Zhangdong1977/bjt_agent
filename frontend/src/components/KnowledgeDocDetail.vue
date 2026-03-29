<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { knowledgeApi } from '@/api/client'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { DocumentContentResponse } from '@/types'
import { Drawer, Tag, Empty } from 'ant-design-vue'

// Shard type for RAG document shards
interface Shard {
  id: string
  content: string
  startLine: number
  endLine: number
}

const props = defineProps<{
  visible: boolean
  docId: string
  docName: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const loading = ref(false)
const content = ref<DocumentContentResponse | null>(null)
const loadingShards = ref(false)
const activeTab = ref('content')
const allShards = ref<Shard[]>([])
const filteredShards = ref<Shard[]>([])
const shardFilter = ref('')

// Debounce timer for shard filtering
let filterDebounceTimer: ReturnType<typeof setTimeout> | null = null

// 将 Markdown 转换为 HTML 并消毒
const htmlContent = computed(() => {
  if (!content.value?.content) return ''
  return DOMPurify.sanitize(marked.parse(content.value.content) as string)
})

// 加载文档内容
async function loadContent() {
  if (!props.docId) return
  loading.value = true
  try {
    const response = await knowledgeApi.getDocumentContent(props.docId)
    content.value = response.data
  } catch (err) {
    console.error('Failed to load content:', err)
  } finally {
    loading.value = false
  }
}

// 监听 visible 变化，加载内容
watch(() => props.visible, (val) => {
  if (val) {
    loadContent()
    shardFilter.value = ''
  } else {
    // Reset shard filter when drawer closes
    shardFilter.value = ''
  }
})

// 加载所有分片
async function loadAllShards() {
  if (!props.docId) return
  loadingShards.value = true
  try {
    const response = await knowledgeApi.getDocumentShards(props.docId)
    allShards.value = response.data.shards || []
    filteredShards.value = allShards.value
  } catch (err) {
    console.error('Failed to load shards:', err)
  } finally {
    loadingShards.value = false
  }
}

// 监听Tab切换
watch(activeTab, (tab) => {
  if (tab === 'rag') {
    loadAllShards()
  } else {
    // Reset filter when switching away from RAG tab
    shardFilter.value = ''
  }
})

// 过滤分片（带防抖）
watch(shardFilter, (filter) => {
  if (filterDebounceTimer) {
    clearTimeout(filterDebounceTimer)
  }
  filterDebounceTimer = setTimeout(() => {
    if (!filter.trim()) {
      filteredShards.value = allShards.value
    } else {
      filteredShards.value = allShards.value.filter((s) =>
        s.content.toLowerCase().includes(filter.toLowerCase())
      )
    }
  }, 300)
})

function close() {
  emit('update:visible', false)
}
</script>

<template>
  <Drawer
    :open="visible"
    :title="docName"
    width="720"
    @close="close"
  >
    <div class="doc-detail">
      <a-spin :spinning="loading">
        <a-tabs v-model:activeKey="activeTab">
          <a-tab-pane key="content" tab="文档内容">
            <div v-if="content" class="content-view" v-html="htmlContent"></div>
            <Empty v-else description="无法加载文档内容" />
          </a-tab-pane>

          <a-tab-pane key="rag" tab="RAG分片">
            <div class="rag-search">
              <a-input
                v-model:value="shardFilter"
                placeholder="在分片中搜索..."
                allow-clear
                style="margin-bottom: 16px;"
              />
              <a-spin :spinning="loadingShards">
                <div v-if="filteredShards.length > 0" class="shards-list">
                  <p class="shard-info">
                    共 {{ filteredShards.length }} 个分片
                  </p>
                  <div v-for="shard in filteredShards" :key="shard.id" class="shard-item">
                    <div class="shard-header">
                      <Tag>分片 {{ shard.id.replace('shard-', '') }}</Tag>
                      <span class="shard-lines">第 {{ shard.startLine }}-{{ shard.endLine }} 行</span>
                    </div>
                    <pre class="shard-content">{{ shard.content }}</pre>
                  </div>
                </div>
                <Empty v-else-if="!loadingShards" description="暂无分片数据" />
              </a-spin>
            </div>
          </a-tab-pane>
        </a-tabs>
      </a-spin>
    </div>
  </Drawer>
</template>

<style scoped>
.doc-detail {
  height: 100%;
}

.content-view {
  max-height: 60vh;
  overflow-y: auto;
  padding: 16px;
  background: #f5f5f5;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
}

.content-view :deep(h1) { font-size: 1.5em; margin: 0.5em 0; }
.content-view :deep(h2) { font-size: 1.3em; margin: 0.5em 0; }
.content-view :deep(p) { margin: 0.5em 0; }

.rag-search {
  padding: 16px 0;
}

.shards-list {
  max-height: 500px;
  overflow-y: auto;
}

.shard-info {
  color: #666;
  margin-bottom: 12px;
}

.shard-item {
  background: #fafafa;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}

.shard-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.shard-lines {
  font-size: 12px;
  color: #999;
}

.shard-content {
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: white;
  padding: 8px;
  border-radius: 4px;
  max-height: 200px;
  overflow-y: auto;
}
</style>
