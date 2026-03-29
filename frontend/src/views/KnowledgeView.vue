<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { knowledgeApi } from '@/api/client'
import { message } from 'ant-design-vue'
import type { UploadFile } from 'ant-design-vue'
import { UploadOutlined, FileTextOutlined, PlusOutlined } from '@ant-design/icons-vue'
import KnowledgeDocDetail from '@/components/KnowledgeDocDetail.vue'

interface KnowledgeDoc {
  id: string
  filename: string
  created_at: string
}

interface GlobalSearchResult {
  docId: string
  source: string
  snippet: string
  score: number
}

// 全局搜索相关
const globalQuery = ref('')
const globalResults = ref<GlobalSearchResult[]>([])
const globalSearching = ref(false)
const globalSearched = ref(false)

// 文档过滤
const docFilter = ref('')
const filteredDocs = computed(() => {
  if (!docFilter.value.trim()) return docs.value
  return docs.value.filter(d =>
    d.filename.toLowerCase().includes(docFilter.value.toLowerCase())
  )
})

const docs = ref<KnowledgeDoc[]>([])
const loading = ref(false)
const uploading = ref(false)
const uploadProgress = ref<number>(0)
const selectedDoc = ref<{ id: string; name: string } | null>(null)
const showDocDetail = ref(false)
const showUpload = ref(false)

onMounted(() => {
  fetchDocs()
})

async function fetchDocs() {
  loading.value = true
  try {
    const response = await knowledgeApi.listDocuments()
    docs.value = response.data.documents || []
  } catch {
    message.error('获取文档列表失败')
  } finally {
    loading.value = false
  }
}

async function handleUpload(info: { file: UploadFile }) {
  const file = info.file.originFileObj as File
  if (!file) return

  uploading.value = true
  uploadProgress.value = 0

  try {
    await knowledgeApi.uploadDocument(file, (progress) => {
      uploadProgress.value = progress.percent
    })
    message.success('文档上传成功')
    uploadProgress.value = 100
    fetchDocs()
    showUpload.value = false
  } catch {
    message.error('文档上传失败')
  } finally {
    uploading.value = false
    uploadProgress.value = 0
  }
}

async function deleteDoc(docId: string) {
  try {
    await knowledgeApi.deleteDocument(docId)
    message.success('文档已删除')
    fetchDocs()
  } catch {
    message.error('删除失败')
  }
}

async function previewDoc(doc: KnowledgeDoc) {
  const token = localStorage.getItem('access_token')
  if (token) {
    window.open(`/api/knowledge/documents/${doc.id}/preview?token=${encodeURIComponent(token)}`, '_blank')
  } else {
    message.error('未登录，无法预览文档')
  }
}

function openDocDetail(doc: KnowledgeDoc) {
  selectedDoc.value = { id: doc.id, name: doc.filename }
  showDocDetail.value = true
}

function toggleUpload() {
  showUpload.value = !showUpload.value
}

async function onGlobalSearch() {
  if (!globalQuery.value.trim()) return
  globalSearching.value = true
  globalSearched.value = true
  try {
    const response = await knowledgeApi.globalSearch(globalQuery.value)
    globalResults.value = response.data.results || []
  } catch (err) {
    console.error('Global search failed:', err)
    message.error('搜索失败，请重试')
    globalResults.value = []
  } finally {
    globalSearching.value = false
  }
}

function openDocFromSearch(docId: string) {
  const doc = docs.value.find(d => d.id === docId)
  if (doc) {
    openDocDetail(doc)
  }
}

function getScoreColor(score: number): string {
  if (score >= 0.9) return 'green'
  if (score >= 0.7) return 'orange'
  return 'red'
}
</script>

<template>
  <div class="knowledge-view">
    <a-breadcrumb class="breadcrumb">
      <a-breadcrumb-item>首页</a-breadcrumb-item>
      <a-breadcrumb-item>知识库</a-breadcrumb-item>
    </a-breadcrumb>

    <div class="knowledge-container">
      <!-- 左侧：搜索区域 (70%) -->
      <div class="search-panel">
        <div class="search-header">
          <h1>知识库搜索</h1>
          <p class="search-subtitle">输入关键词搜索文档内容</p>
        </div>

        <div class="search-box">
          <a-input-search
            v-model:value="globalQuery"
            placeholder="输入关键词搜索..."
            size="large"
            @search="onGlobalSearch"
          >
            <template #enterButton>
              <a-button type="primary" :loading="globalSearching">搜索</a-button>
            </template>
          </a-input-search>
        </div>

        <div class="search-results">
          <a-spin :spinning="globalSearching">
            <div v-if="globalResults.length > 0" class="results-list">
              <p class="results-count">
                找到 {{ globalResults.length }} 个结果
              </p>
              <div
                v-for="(result, idx) in globalResults"
                :key="idx"
                class="result-item"
                @click="openDocFromSearch(result.docId)"
              >
                <div class="result-header">
                  <file-text-outlined style="color: #6366f1" />
                  <span class="result-source">{{ result.source }}</span>
                  <a-tag :color="getScoreColor(result.score)">{{ result.score.toFixed(2) }}</a-tag>
                </div>
                <p class="result-snippet">{{ result.snippet }}</p>
              </div>
            </div>
            <a-empty
              v-else-if="globalSearched && !globalSearching"
              description="未找到相关结果"
            />
            <div v-else class="search-hint">
              <p>输入关键词开始搜索知识库</p>
            </div>
          </a-spin>
        </div>
      </div>

      <!-- 右侧：文档列表 (30%) -->
      <div class="doc-panel">
        <div class="doc-panel-header">
          <h3>文档列表</h3>
          <a-button type="primary" @click="toggleUpload">
            <template #icon><PlusOutlined /></template>
            上传文档
          </a-button>
        </div>

        <a-input
          v-model:value="docFilter"
          placeholder="搜索文档..."
          allow-clear
          style="margin-bottom: 12px;"
        />

        <a-list
          :loading="loading"
          :dataSource="filteredDocs"
          item-layout="horizontal"
          :pagination="{ pageSize: 10 }"
        >
          <template #renderItem="{ item }">
            <a-list-item>
              <template #actions>
                <a @click="previewDoc(item)">预览</a>
                <a @click="openDocDetail(item)">详情</a>
                <a-popconfirm
                  title="确定要删除此文档吗？"
                  @confirm="deleteDoc(item.id)"
                >
                  <a class="delete-link" href="javascript:void(0)">删除</a>
                </a-popconfirm>
              </template>
              <a-list-item-meta>
                <template #avatar>
                  <file-text-outlined style="font-size: 24px; color: #6366f1" />
                </template>
                <template #title>{{ item.filename }}</template>
                <template #description>
                  上传于 {{ new Date(item.created_at).toLocaleString() }}
                </template>
              </a-list-item-meta>
            </a-list-item>
          </template>

          <template #emptyText>
            <a-empty description="暂无上传文档" />
          </template>
        </a-list>
      </div>
    </div>

    <!-- 上传 Drawer -->
    <a-drawer
      v-model:open="showUpload"
      title="上传文档"
      width="500"
    >
      <div v-if="uploading" class="upload-progress">
        <a-progress :percent="uploadProgress" status="active" />
        <p>正在上传...</p>
      </div>
      <a-upload-dragger
        v-else
        name="file"
        :multiple="true"
        :show-upload-list="false"
        accept=".pdf,.doc,.docx"
        @change="handleUpload"
      >
        <p class="ant-upload-drag-icon">
          <upload-outlined />
        </p>
        <p class="ant-upload-text">点击或拖拽上传企业产品或资质文档</p>
        <p class="ant-upload-hint">支持 PDF、Word 格式</p>
      </a-upload-dragger>
    </a-drawer>

    <!-- 文档详情 Drawer -->
    <KnowledgeDocDetail
      v-model:visible="showDocDetail"
      :doc-id="selectedDoc?.id || ''"
      :doc-name="selectedDoc?.name || ''"
    />
  </div>
</template>

<style scoped>
.knowledge-view {
  padding: 0 24px 24px;
}

.breadcrumb {
  margin-bottom: 24px;
}

.knowledge-container {
  display: flex;
  gap: 24px;
  height: calc(100vh - 180px);
}

.search-panel {
  flex: 7;
  display: flex;
  flex-direction: column;
  padding: 24px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.search-header {
  text-align: center;
  margin-bottom: 24px;
}

.search-header h1 {
  font-size: 32px;
  margin-bottom: 8px;
  color: #1a1a1a;
}

.search-subtitle {
  color: #666;
  margin-bottom: 0;
}

.search-box {
  max-width: 600px;
  margin: 0 auto 24px;
  width: 100%;
}

.search-results {
  flex: 1;
  overflow-y: auto;
  padding: 0 20px;
}

.results-count {
  color: #666;
  margin-bottom: 16px;
}

.result-item {
  background: #fafafa;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
  cursor: pointer;
  transition: background 0.2s;
}

.result-item:hover {
  background: #f0f0f0;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.result-source {
  flex: 1;
  font-weight: 500;
  color: #1890ff;
}

.result-snippet {
  font-size: 13px;
  line-height: 1.6;
  color: #333;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.search-hint {
  text-align: center;
  color: #999;
  padding: 60px 0;
}

.doc-panel {
  flex: 3;
  display: flex;
  flex-direction: column;
  padding: 24px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  min-width: 320px;
}

.doc-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.doc-panel-header h3 {
  margin: 0;
}

.doc-panel :deep(.ant-list-item) {
  padding: 12px 0;
}

.delete-link {
  color: #ff4d4f;
}

.delete-link:hover {
  color: #d9363e;
}

.upload-progress {
  padding: 24px;
  text-align: center;
}

.upload-progress p {
  margin-top: 16px;
  color: #666;
}
</style>