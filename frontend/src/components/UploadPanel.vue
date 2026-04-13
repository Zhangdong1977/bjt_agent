<script setup lang="ts">
import { ref } from 'vue'
import type { Document } from '@/types'

defineProps<{
  document: Document | null
  documentType: 'tender' | 'bid'
  label: string
}>()

const emit = defineEmits<{
  upload: [file: File]
  delete: [documentId: string]
  view: [documentId: string]
}>()

const uploading = ref(false)

function getStatusClass(status: string) {
  switch (status) {
    case 'parsed':
      return 'status-success'
    case 'parsing':
      return 'status-running'
    case 'failed':
      return 'status-error'
    default:
      return 'status-pending'
  }
}

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  uploading.value = true
  try {
    emit('upload', file)
  } finally {
    uploading.value = false
    input.value = ''
  }
}
</script>

<template>
  <div class="document-card">
    <h3>{{ label }}</h3>
    <div v-if="document" class="document-info">
      <div class="doc-header">
        <p class="filename">{{ document.original_filename }}</p>
        <span :class="['status', getStatusClass(document.status)]">
          {{ document.status }}
        </span>
      </div>
      <p v-if="document.page_count" class="doc-meta">
        {{ document.page_count }} 页, {{ document.word_count }} 字
      </p>
      <div class="button-group">
        <button
          v-if="document.status === 'parsed'"
          class="view-btn"
          @click="emit('view', document.id)"
        >
          查看内容
        </button>
        <button
          class="delete-btn"
          @click="emit('delete', document.id)"
        >
          删除
        </button>
      </div>
    </div>
    <div v-else class="upload-area">
      <input
        type="file"
        accept=".pdf,.docx,.doc"
        :id="`${documentType}-upload`"
        class="file-input"
        @change="handleUpload"
      />
      <label :for="`${documentType}-upload`" class="upload-label">
        <span v-if="uploading">上传中...</span>
        <span v-else>点击上传 PDF 或 Word 文件</span>
      </label>
    </div>
  </div>
</template>

<style scoped>
.document-card {
  border: 2px dashed var(--line);
  border-radius: var(--r2);
  padding: 1.5rem;
  text-align: center;
  background: var(--bg1);
}

.document-card h3 {
  color: var(--text);
  margin-bottom: 1rem;
}

.document-info {
  text-align: left;
}

.doc-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.filename {
  color: var(--text);
  font-weight: 500;
  word-break: break-all;
}

.doc-meta {
  color: var(--muted);
  font-size: 0.9rem;
  margin: 0.5rem 0;
}

.upload-area {
  position: relative;
}

.file-input {
  position: absolute;
  width: 100%;
  height: 100%;
  top: 0;
  left: 0;
  opacity: 0;
  cursor: pointer;
}

.upload-label {
  display: block;
  padding: 2rem;
  border: 2px dashed var(--purple);
  border-radius: var(--r2);
  color: var(--purple);
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.upload-label:hover {
  background: var(--purple-bg);
}

.status {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
}

.status-pending {
  background: var(--bg3);
  color: var(--muted);
}

.status-running {
  background: var(--amber-bg);
  color: var(--amber);
}

.status-success {
  background: var(--green-bg);
  color: var(--green);
}

.status-error {
  background: var(--red-bg);
  color: var(--red);
}

.button-group {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.view-btn,
.delete-btn {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  font-size: 0.85rem;
  flex: 1;
}

.view-btn {
  background: var(--purple);
  color: var(--white);
  transition: filter 0.2s ease;
}

.delete-btn {
  background: var(--red);
  color: var(--white);
  transition: filter 0.2s ease;
}

.view-btn:hover {
  filter: brightness(1.1);
}

.delete-btn:hover {
  filter: brightness(1.1);
}
</style>
