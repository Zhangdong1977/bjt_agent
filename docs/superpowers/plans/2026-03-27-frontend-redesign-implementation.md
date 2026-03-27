# 前端重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构前端页面，新增标书检查/历史标书/知识库三个模块，使用 Ant Design Vue 提升界面美感，重点美化时间线控件。

**Architecture:** 采用左侧Sidebar固定 + 右侧内容区的布局，新路由结构 `/home/check`、`/home/history`、`/home/knowledge`，项目详情页保持现有路由。

**Tech Stack:** Ant Design Vue 4.x, Vue 3, Pinia, Vue Router 4

---

## 阶段1：基础设施

### Task 1: 安装 Ant Design Vue

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: 添加 Ant Design Vue 依赖**

```bash
cd /home/openclaw/bjt_agent/frontend
npm install ant-design-vue@4 @ant-design/icons-vue
```

- [ ] **Step 2: 提交**

```bash
git add package.json package-lock.json
git commit -m "chore: add ant-design-vue dependency"
```

---

### Task 2: 创建主布局组件 AppLayout

**Files:**
- Create: `frontend/src/components/AppLayout.vue`
- Create: `frontend/src/components/AppSidebar.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/router/index.ts`

- [ ] **Step 1: 创建 AppSidebar.vue**

```vue
<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { computed } from 'vue'

const router = useRouter()
const route = useRoute()

const menuItems = [
  { key: '/home/check', label: '标书检查', icon: 'file-search' },
  { key: '/home/history', label: '历史标书', icon: 'history' },
  { key: '/home/knowledge', label: '知识库', icon: 'book' },
]

const selectedKey = computed(() => route.path)

function navigate(path: string) {
  router.push(path)
}
</script>

<template>
  <a-menu
    v-model:selectedKeys="[selectedKey]"
    mode="inline"
    theme="light"
    class="app-sidebar"
    @click="({ key }) => navigate(key)"
  >
    <a-menu-item v-for="item in menuItems" :key="item.key">
      <template #icon>
        <component :is="$antIcons[item.icon]" />
      </template>
      {{ item.label }}
    </a-menu-item>
  </a-menu>
</template>

<style scoped>
.app-sidebar {
  height: 100%;
  background: #fafafa;
  border-right: 1px solid #e8e8e8;
}

.app-sidebar :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
}

.app-sidebar :deep(.ant-menu-item-selected) {
  background: linear-gradient(90deg, rgba(99, 102, 241, 0.1) 0%, transparent 100%);
  border-left: 2px solid #6366f1;
}
</style>
```

- [ ] **Step 2: 创建 AppLayout.vue**

```vue
<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppSidebar from './AppSidebar.vue'

const router = useRouter()
const authStore = useAuthStore()

function logout() {
  authStore.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <a-layout class="app-layout">
    <a-layout-header class="app-header">
      <div class="header-left">
        <h1 class="logo">Bid Review Agent</h1>
      </div>
      <div class="header-right">
        <span class="username">{{ authStore.user?.username }}</span>
        <a-button type="text" danger @click="logout">Logout</a-button>
      </div>
    </a-layout-header>
    <a-layout>
      <a-layout-sider width="200" class="app-sider">
        <AppSidebar />
      </a-layout-sider>
      <a-layout-content class="app-content">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: white;
  padding: 0 24px;
  border-bottom: 1px solid #e8e8e8;
  height: 64px;
}

.header-left .logo {
  color: #6366f1;
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.username {
  color: #666;
}

.app-sider {
  background: #fafafa;
}

.app-content {
  padding: 24px;
  background: #f5f3ff;
  min-height: calc(100vh - 64px);
}
</style>
```

- [ ] **Step 3: 更新 router/index.ts**

```typescript
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { guest: true }
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/views/RegisterView.vue'),
      meta: { guest: true }
    },
    {
      path: '/',
      redirect: '/home/check'
    },
    {
      path: '/home',
      component: () => import('@/components/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          redirect: '/home/check'
        },
        {
          path: 'check',
          name: 'check',
          component: () => import('@/views/CheckView.vue')
        },
        {
          path: 'history',
          name: 'history',
          component: () => import('@/views/HistoryView.vue')
        },
        {
          path: 'knowledge',
          name: 'knowledge',
          component: () => import('@/views/KnowledgeView.vue')
        }
      ]
    },
    {
      path: '/projects/:id',
      name: 'project',
      component: () => import('@/views/ProjectView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/projects/:id/review',
      name: 'review-timeline',
      component: () => import('@/views/ReviewTimelineView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/projects/:id/results',
      name: 'review-results',
      component: () => import('@/views/ResultsView.vue'),
      meta: { requiresAuth: true }
    }
  ]
})

router.beforeEach(async (to, _from, next) => {
  const authStore = useAuthStore()

  if (!authStore.initialized) {
    await authStore.initialize()
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    next({ name: 'login' })
  } else if (to.meta.guest && authStore.isAuthenticated) {
    next({ name: 'home' })
  } else {
    next()
  }
})

export default router
```

- [ ] **Step 4: 更新 App.vue**

```vue
<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

onMounted(() => {
  authStore.initialize()
})
</script>

<template>
  <ConfigProvider theme="{ token: { colorPrimary: '#6366f1' } }">
    <RouterView />
  </ConfigProvider>
</template>

<script lang="ts">
import { ConfigProvider } from 'ant-design-vue'
export default {
  components: { ConfigProvider }
}
</script>

<style>
@import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&family=Poppins:wght@500;600;700&display=swap');

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f5f3ff;
  color: #1e1b4b;
  line-height: 1.6;
}

h1, h2, h3, h4, h5, h6 {
  font-family: 'Poppins', sans-serif;
  font-weight: 600;
}

#app {
  min-height: 100vh;
}
</style>
```

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/AppLayout.vue frontend/src/components/AppSidebar.vue
git add frontend/src/router/index.ts frontend/src/App.vue
git commit -m "feat: add AppLayout with sidebar navigation"
```

---

## 阶段2：业务模块

### Task 3: 创建 CheckView（标书检查）

**Files:**
- Create: `frontend/src/views/CheckView.vue`

- [ ] **Step 1: 创建 CheckView.vue**

```vue
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
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/views/CheckView.vue
git commit -m "feat: add CheckView for bid checking"
```

---

### Task 4: 创建 HistoryView（历史标书）

**Files:**
- Create: `frontend/src/views/HistoryView.vue`

- [ ] **Step 1: 创建 HistoryView.vue**

```vue
<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { message } from 'ant-design-vue'

const router = useRouter()
const projectStore = useProjectStore()

const searchText = ref('')
const selectedStatus = ref<string | null>(null)

onMounted(() => {
  projectStore.fetchProjects()
})

const filteredProjects = computed(() => {
  let projects = projectStore.projects

  if (searchText.value) {
    const keyword = searchText.value.toLowerCase()
    projects = projects.filter(p =>
      p.name.toLowerCase().includes(keyword)
    )
  }

  if (selectedStatus.value) {
    projects = projects.filter(p => p.status === selectedStatus.value)
  }

  return projects
})

function goToProject(projectId: string) {
  router.push({ name: 'review-results', params: { id: projectId } })
}

async function deleteProject(projectId: string, event: Event) {
  event.stopPropagation()
  if (confirm('确定要删除此项目吗？')) {
    await projectStore.deleteProject(projectId)
    message.success('项目已删除')
  }
}

function getStatusColor(status: string) {
  const colorMap: Record<string, string> = {
    completed: 'success',
    running: 'processing',
    failed: 'error',
    pending: 'default'
  }
  return colorMap[status] || 'default'
}
</script>

<template>
  <div class="history-view">
    <a-breadcrumb class="breadcrumb">
      <a-breadcrumb-item>首页</a-breadcrumb-item>
      <a-breadcrumb-item>历史标书</a-breadcrumb-item>
    </a-breadcrumb>

    <a-card class="filter-card" :bordered="false">
      <div class="filters">
        <a-input-search
          v-model:value="searchText"
          placeholder="搜索项目名称"
          style="width: 300px"
          allow-clear
        />
        <a-select
          v-model:value="selectedStatus"
          placeholder="筛选状态"
          style="width: 150px"
          allow-clear
        >
          <a-select-option value="completed">已完成</a-select-option>
          <a-select-option value="running">进行中</a-select-option>
          <a-select-option value="failed">失败</a-select-option>
        </a-select>
      </div>
    </a-card>

    <a-card class="list-card" :bordered="false">
      <a-table
        :dataSource="filteredProjects"
        :columns="[
          { title: '项目名称', dataIndex: 'name', key: 'name' },
          { title: '状态', dataIndex: 'status', key: 'status' },
          { title: '创建时间', dataIndex: 'created_at', key: 'created_at' },
          { title: '操作', key: 'action' }
        ]"
        :pagination="{ pageSize: 10 }"
        row-key="id"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <a @click="goToProject(record.id)" class="project-link">
              {{ record.name }}
            </a>
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="getStatusColor(record.status)">
              {{ record.status }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'created_at'">
            {{ new Date(record.created_at).toLocaleDateString() }}
          </template>
          <template v-else-if="column.key === 'action'">
            <a-space>
              <a @click="goToProject(record.id)">查看结果</a>
              <a-divider type="vertical" />
              <a-popconfirm
                title="确定要删除此项目吗？"
                @confirm="deleteProject(record.id, $event)"
              >
                <a class="delete-link">删除</a>
              </a-popconfirm>
            </a-space>
          </template>
        </template>

        <template #emptyText>
          <a-empty description="暂无历史项目" />
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<style scoped>
.history-view {
  max-width: 1200px;
  margin: 0 auto;
}

.breadcrumb {
  margin-bottom: 24px;
}

.filter-card,
.list-card {
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  margin-bottom: 24px;
}

.filters {
  display: flex;
  gap: 16px;
  align-items: center;
}

.project-link {
  color: #6366f1;
  font-weight: 500;
}

.project-link:hover {
  color: #4f46e5;
}

.delete-link {
  color: #ff4d4f;
}

.delete-link:hover {
  color: #d9363e;
}
</style>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/views/HistoryView.vue
git commit -m "feat: add HistoryView for past projects"
```

---

### Task 5: 创建 KnowledgeView（知识库）

**Files:**
- Create: `frontend/src/views/KnowledgeView.vue`

- [ ] **Step 1: 创建 KnowledgeView.vue**

```vue
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { knowledgeApi } from '@/api/client'
import { message } from 'ant-design-vue'
import type { UploadFile } from 'ant-design-vue'

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
    docs.value = response.data || []
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
  window.open(`/api/knowledge/${doc.id}/preview`, '_blank')
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

<script lang="ts">
import { UploadOutlined, FileTextOutlined } from '@ant-design/icons-vue'
import { knowledgeApi } from '@/api/client'

export default {
  components: { UploadOutlined, FileTextOutlined },
  methods: {
    async listDocuments() {
      // Placeholder - implement actual API call
      return { data: [] }
    },
    async deleteDocument(id: string) {
      // Placeholder - implement actual API call
    }
  }
}
</script>

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
```

- [ ] **Step 2: 需要在 api/client.ts 中添加 knowledgeApi**

```typescript
// 在 api/client.ts 中添加
export const knowledgeApi = {
  listDocuments: () => api.get('/api/knowledge/documents'),
  deleteDocument: (id: string) => api.delete(`/api/knowledge/documents/${id}`),
  uploadDocument: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/api/knowledge/upload', formData)
  }
}
```

- [ ] **Step 3: 提交**

```bash
git add frontend/src/views/KnowledgeView.vue frontend/src/api/client.ts
git commit -m "feat: add KnowledgeView for enterprise documents"
```

---

## 阶段3：UI美化

### Task 6: 美化 ReviewTimelineView（时间线）

**Files:**
- Modify: `frontend/src/views/ReviewTimelineView.vue`
- Create: `frontend/src/components/ReviewTimeline.vue`

- [ ] **Step 1: 创建 ReviewTimeline.vue 组件**

```vue
<script setup lang="ts">
import { computed } from 'vue'

interface TimelineStep {
  step_number: number
  step_type: 'tool_call' | 'observation' | 'thought'
  tool_name?: string
  content: string
  status?: 'pending' | 'running' | 'completed' | 'error'
}

const props = defineProps<{
  steps: TimelineStep[]
  currentStatus: 'pending' | 'running' | 'completed' | 'error'
}>()

function getStepStatus(step: TimelineStep, index: number) {
  if (props.currentStatus === 'pending') return 'pending'
  if (props.currentStatus === 'completed' || props.currentStatus === 'error') return 'completed'
  if (index < props.steps.length - 1) return 'completed'
  return 'running'
}

function getStepIcon(step: TimelineStep, status: string) {
  if (status === 'running') return 'loading'
  if (status === 'completed') {
    if (step.step_type === 'tool_call') return 'tool'
    if (step.step_type === 'observation') return 'eye'
    return 'bulb'
  }
  return 'clock'
}

function getBorderColor(step: TimelineStep) {
  if (step.step_type === 'tool_call') return '#fa8c16'
  if (step.step_type === 'observation') return '#52c41a'
  return '#1890ff'
}

function getIconColor(step: TimelineStep) {
  if (step.step_type === 'tool_call') return '#fa8c16'
  if (step.step_type === 'observation') return '#52c41a'
  return '#1890ff'
}
</script>

<template>
  <div class="review-timeline">
    <div class="timeline-container">
      <div
        v-for="(step, index) in steps"
        :key="step.step_number"
        class="timeline-item"
      >
        <div :class="['timeline-node', `status-${getStepStatus(step, index)}`]">
          <template v-if="getStepStatus(step, index) === 'running'">
            <loading-outlined class="loading-icon" />
          </template>
          <template v-else-if="getStepStatus(step, index) === 'completed'">
            <check-outlined v-if="step.step_type !== 'tool_call'" />
            <tool-outlined v-else />
          </template>
          <template v-else>
            <clock-circle-outlined />
          </template>
        </div>

        <div
          v-if="index < steps.length - 1"
          :class="['timeline-line', `line-${getStepStatus(steps[index + 1], index + 1)}`]"
        />

        <div class="timeline-content-card" :style="{ borderLeftColor: getBorderColor(step) }">
          <div class="step-header">
            <span class="step-type" :style="{ color: getIconColor(step) }">
              <tool-outlined v-if="step.step_type === 'tool_call'" />
              <eye-outlined v-else-if="step.step_type === 'observation'" />
              <bulb-outlined v-else />
              {{ step.step_type === 'tool_call' ? (step.tool_name || 'Tool') : (step.step_type === 'observation' ? 'Observation' : 'Thought') }}
            </span>
            <span class="step-number">#{{ step.step_number }}</span>
          </div>
          <p class="step-content">{{ step.content }}</p>
        </div>
      </div>

      <div v-if="steps.length === 0" class="timeline-empty">
        <a-empty description="暂无审查步骤" />
      </div>
    </div>
  </div>
</template>

<script lang="ts">
import {
  CheckOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  ToolOutlined,
  EyeOutlined,
  BulbOutlined
} from '@ant-design/icons-vue'
import { a Empty } from 'ant-design-vue'

export default {
  components: {
    CheckOutlined,
    ClockCircleOutlined,
    LoadingOutlined,
    ToolOutlined,
    EyeOutlined,
    BulbOutlined,
    'a-empty': Empty
  }
}
</script>

<style scoped>
.review-timeline {
  padding: 24px 0;
}

.timeline-container {
  position: relative;
  padding-left: 60px;
}

.timeline-item {
  position: relative;
  padding-bottom: 32px;
}

.timeline-item:last-child {
  padding-bottom: 0;
}

.timeline-node {
  position: absolute;
  left: 0;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  z-index: 1;
}

.timeline-node.status-pending {
  background: #f5f5f5;
  border: 2px dashed #d9d9d9;
  color: #999;
}

.timeline-node.status-running {
  background: #fff7e6;
  border: 2px solid #faad14;
  color: #faad14;
}

.timeline-node.status-completed {
  background: #f6ffed;
  border: 2px solid #52c41a;
  color: #52c41a;
}

.timeline-node.status-error {
  background: #fff2f0;
  border: 2px solid #ff4d4f;
  color: #ff4d4f;
}

.loading-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.timeline-line {
  position: absolute;
  left: 17px;
  top: 36px;
  width: 2px;
  height: 32px;
}

.timeline-line.line-pending {
  background: repeating-linear-gradient(
    to bottom,
    #d9d9d9 0px,
    #d9d9d9 4px,
    transparent 4px,
    transparent 8px
  );
}

.timeline-line.line-running {
  background: linear-gradient(to bottom, #52c41a, #faad14);
  animation: pulse 1.5s ease-in-out infinite;
}

.timeline-line.line-completed {
  background: #52c41a;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.timeline-content-card {
  background: white;
  border-radius: 8px;
  padding: 16px;
  border-left: 4px solid;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  transition: all 0.3s ease;
}

.timeline-content-card:hover {
  box-shadow: 0 4px 16px rgba(99, 102, 241, 0.15);
  transform: translateX(4px);
}

.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.step-type {
  font-weight: 600;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.step-number {
  color: #999;
  font-size: 12px;
}

.step-content {
  margin: 0;
  color: #333;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.timeline-empty {
  text-align: center;
  padding: 48px 0;
}
</style>
```

- [ ] **Step 2: 更新 ReviewTimelineView.vue 使用新组件**

在 ReviewTimelineView.vue 中:
1. 导入 ReviewTimeline 组件
2. 替换现有的 timeline 内容使用 `<ReviewTimeline :steps="projectStore.agentSteps" :current-status="projectStore.currentTask?.status || 'pending'" />`

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/ReviewTimeline.vue frontend/src/views/ReviewTimelineView.vue
git commit -m "feat: add enhanced ReviewTimeline component with animations"
```

---

## 阶段4：测试验证

### Task 7: 测试新页面和路由

- [ ] **Step 1: 启动开发服务器**

```bash
cd frontend && npm run dev
```

- [ ] **Step 2: 测试路由**

访问以下URL验证页面正常加载:
- `/home/check` - 标书检查页面
- `/home/history` - 历史标书页面
- `/home/knowledge` - 知识库页面

- [ ] **Step 3: 测试侧边栏导航**

点击各菜单项，确认内容区正常切换

- [ ] **Step 4: 提交最终状态**

```bash
git add -A
git commit -m "feat: complete frontend redesign with new layout and components"
```

---

## 实施检查清单

- [ ] Task 1: Ant Design Vue 依赖安装
- [ ] Task 2: 主布局组件创建
- [ ] Task 3: CheckView 实现
- [ ] Task 4: HistoryView 实现
- [ ] Task 5: KnowledgeView 实现
- [ ] Task 6: 时间线组件美化
- [ ] Task 7: 测试验证
