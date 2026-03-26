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
      <p class="filename">{{ document.original_filename }}</p>
      <span :class="['status', getStatusClass(document.status)]">
        {{ document.status }}
      </span>
      <p v-if="document.page_count" class="doc-meta">
        {{ document.page_count }} pages, {{ document.word_count }} words
      </p>
      <div class="button-group">
        <button
          v-if="document.status === 'parsed'"
          class="view-btn"
          @click="emit('view', document.id)"
        >
          View Content
        </button>
        <button
          class="delete-btn"
          @click="emit('delete', document.id)"
        >
          Delete
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
        <span v-if="uploading">Uploading...</span>
        <span v-else>Click to upload PDF or Word</span>
      </label>
    </div>
  </div>
</template>

<style scoped>
.document-card {
  border: 2px dashed #ddd;
  border-radius: 8px;
  padding: 1.5rem;
  text-align: center;
}

.document-card h3 {
  color: #333;
  margin-bottom: 1rem;
}

.document-info {
  text-align: left;
}

.filename {
  color: #333;
  font-weight: 500;
  word-break: break-all;
}

.doc-meta {
  color: #666;
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
  border: 2px dashed #667eea;
  border-radius: 8px;
  color: #667eea;
  cursor: pointer;
}

.upload-label:hover {
  background: #f8f8ff;
}

.status {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
  margin-top: 0.5rem;
}

.status-pending {
  background: #ddd;
  color: #666;
}

.status-running {
  background: #f6e05e;
  color: #744210;
}

.status-success {
  background: #68d391;
  color: #22543d;
}

.status-error {
  background: #fc8181;
  color: #742a2a;
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
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
}

.view-btn {
  background: #667eea;
  color: white;
}

.delete-btn {
  background: #e53e3e;
  color: white;
}

.view-btn:hover {
  background: #5568d3;
}

.delete-btn:hover {
  background: #c53030;
}
</style>
