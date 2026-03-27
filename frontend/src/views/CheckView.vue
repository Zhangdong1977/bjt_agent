<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { ElMessage } from 'element-plus'

const router = useRouter()
const projectStore = useProjectStore()

const loading = ref(false)
const formState = ref({
  name: '',
  description: ''
})

async function createProject() {
  if (!formState.value.name.trim()) {
    ElMessage.warning('请输入项目名称')
    return
  }

  loading.value = true
  try {
    const project = await projectStore.createProject(
      formState.value.name,
      formState.value.description || undefined
    )
    if (project) {
      router.push({ name: 'project', params: { id: project.id } })
    }
  } catch {
    ElMessage.error('创建项目失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="check-view">
    <a-breadcrumb class="breadcrumb">
      <a-breadcrumb-item>首页</a-breadcrumb-item>
      <a-breadcrumb-item>标书检查</a-breadcrumb-item>
    </a-breadcrumb>

    <a-card class="create-card" :bordered="false">
      <template #title>
        <span class="card-title">创建新项目</span>
      </template>

      <a-form layout="vertical" :model="formState">
        <a-form-item label="项目名称" required>
          <a-input
            v-model:value="formState.name"
            placeholder="请输入项目名称"
            size="large"
          />
        </a-form-item>

        <a-form-item label="项目描述">
          <a-textarea
            v-model:value="formState.description"
            placeholder="请输入项目描述（可选）"
            :rows="4"
          />
        </a-form-item>

        <a-form-item>
          <a-button
            type="primary"
            size="large"
            :loading="loading"
            @click="createProject"
          >
            创建并上传文档
          </a-button>
        </a-form-item>
      </a-form>
    </a-card>

    <a-card class="help-card" :bordered="false">
      <template #title>使用说明</template>
      <ol class="help-list">
        <li>创建新项目，填写项目名称和描述</li>
        <li>上传招标书（PDF或Word文档）</li>
        <li>上传应标书（PDF或Word文档）</li>
        <li>点击"开始审查"启动AI审查流程</li>
        <li>查看审查结果，导出报告</li>
      </ol>
    </a-card>
  </div>
</template>

<style scoped>
.check-view {
  max-width: 800px;
  margin: 0 auto;
}

.breadcrumb {
  margin-bottom: 24px;
}

.create-card {
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.create-card:hover {
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.15);
}

.card-title {
  font-size: 18px;
  font-weight: 600;
  color: #1e1b4b;
}

.help-card {
  margin-top: 24px;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.help-list {
  margin: 0;
  padding-left: 20px;
  color: #666;
  line-height: 2;
}
</style>