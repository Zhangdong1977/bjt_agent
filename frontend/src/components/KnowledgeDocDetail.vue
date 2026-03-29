<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { knowledgeApi } from '@/api/client'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { RAGSearchResult, DocumentContentResponse } from '@/types'
import { Drawer, Tag, Empty } from 'ant-design-vue'

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
const ragQuery = ref('')
const ragResults = ref<RAGSearchResult[]>([])
const searching = ref(false)

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

// 执行RAG搜索
async function searchRAG() {
  if (!ragQuery.value.trim()) return
  searching.value = true
  try {
    const response = await knowledgeApi.ragSearch(ragQuery.value)
    ragResults.value = response.data.results || []
  } catch (err) {
    console.error('RAG search failed:', err)
  } finally {
    searching.value = false
  }
}

// 监听 visible 变化，加载内容
watch(() => props.visible, (val) => {
  if (val) {
    loadContent()
    ragQuery.value = ''
    ragResults.value = []
  }
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
        <a-tabs>
          <a-tab-pane key="content" tab="文档内容">
            <div v-if="content" class="content-view" v-html="htmlContent"></div>
            <Empty v-else description="无法加载文档内容" />
          </a-tab-pane>

          <a-tab-pane key="rag" tab="RAG分片">
            <div class="rag-search">
              <a-input-search
                v-model:value="ragQuery"
                placeholder="输入搜索内容测试RAG..."
                enter-button="搜索"
                @search="searchRAG"
              />
              <a-spin :spinning="searching">
                <div v-if="ragResults.length > 0" class="rag-results">
                  <p class="search-info">
                    找到 {{ ragResults.length }} 个结果 ({{ (ragResults[0]?.score || 0).toFixed(2) }} 相似度)
                  </p>
                  <div v-for="(result, idx) in ragResults" :key="idx" class="rag-item">
                    <div class="rag-header">
                      <Tag>{{ result.score.toFixed(2) }}</Tag>
                      <span class="rag-source">{{ result.source }}</span>
                    </div>
                    <p class="rag-snippet">{{ result.snippet }}</p>
                  </div>
                </div>
                <Empty v-else description="输入内容搜索RAG分片" />
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

.rag-results {
  margin-top: 16px;
}

.search-info {
  color: #666;
  margin-bottom: 12px;
}

.rag-item {
  background: #f5f5f5;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}

.rag-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.rag-source {
  font-size: 12px;
  color: #666;
}

.rag-snippet {
  font-size: 14px;
  line-height: 1.5;
  margin: 0;
  color: #333;
}
</style>
