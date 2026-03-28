<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { knowledgeApi } from '@/api/client'
import { message } from 'ant-design-vue'
import type { UploadFile } from 'ant-design-vue'
import { UploadOutlined, FileTextOutlined } from '@ant-design/icons-vue'

interface KnowledgeDoc {
  id: string
  filename: string
  created_at: string
  file_type: string
  size: number
}

const docs = ref<KnowledgeDoc[]>([])
const loading = ref(false)
const uploading = ref(false)

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
  if (info.file.status === 'uploading') {
    uploading.value = true
  } else if (info.file.status === 'done') {
    uploading.value = false
    message.success('文档上传成功')
    fetchDocs()
  } else if (info.file.status === 'error') {
    uploading.value = false
    message.error('文档上传失败')
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
  window.open(`/api/knowledge/documents/${doc.id}/preview`, '_blank')
}
</script>

<template>
  <div class="knowledge-view">
    <a-breadcrumb class="breadcrumb">
      <a-breadcrumb-item>首页</a-breadcrumb-item>
      <a-breadcrumb-item>知识库</a-breadcrumb-item>
    </a-breadcrumb>

    <a-card class="upload-card" :bordered="false">
      <a-upload-dragger
        name="file"
        :multiple="true"
        action="/api/knowledge/upload"
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
    </a-card>

    <a-card class="list-card" :bordered="false">
      <template #title>已上传文档</template>

      <a-list
        :loading="loading"
        :dataSource="docs"
        item-layout="horizontal"
      >
        <template #renderItem="{ item }">
          <a-list-item>
            <template #actions>
              <a @click="previewDoc(item)">预览</a>
              <a-divider type="vertical" />
              <a-popconfirm
                title="确定要删除此文档吗？"
                @confirm="deleteDoc(item.id)"
              >
                <a class="delete-link">删除</a>
              </a-popconfirm>
            </template>
            <a-list-item-meta>
              <template #avatar>
                <file-text-outlined style="font-size: 24px; color: #6366f1" />
              </template>
              <template #title>
                {{ item.filename }}
              </template>
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
    </a-card>
  </div>
</template>

<style scoped>
.knowledge-view {
  max-width: 1000px;
  margin: 0 auto;
}

.breadcrumb {
  margin-bottom: 24px;
}

.upload-card {
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  margin-bottom: 24px;
}

.list-card {
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.delete-link {
  color: #ff4d4f;
}

.delete-link:hover {
  color: #d9363e;
}
</style>