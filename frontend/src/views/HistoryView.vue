<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { message } from 'ant-design-vue'

const router = useRouter()
const projectStore = useProjectStore()

const searchText = ref('')

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

  return projects
})

function goToProject(projectId: string) {
  router.push({ name: 'project', params: { id: projectId } })
}

async function deleteProject(projectId: string, event: Event) {
  event.stopPropagation()
  if (confirm('确定要删除此项目吗？')) {
    await projectStore.deleteProject(projectId)
    message.success('项目已删除')
  }
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
      </div>
    </a-card>

    <a-card class="list-card" :bordered="false">
      <a-table
        :dataSource="filteredProjects"
        :columns="[
          { title: '项目名称', dataIndex: 'name', key: 'name' },
          { title: '描述', dataIndex: 'description', key: 'description' },
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
          <template v-else-if="column.key === 'description'">
            {{ record.description || '-' }}
          </template>
          <template v-else-if="column.key === 'created_at'">
            {{ new Date(record.created_at).toLocaleDateString() }}
          </template>
          <template v-else-if="column.key === 'action'">
            <a-space>
              <a @click="goToProject(record.id)">查看详情</a>
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
  color: var(--purple);
  font-weight: 500;
}

.project-link:hover {
  color: var(--purple-dim);
}

.delete-link {
  color: var(--red);
}

.delete-link:hover {
  color: var(--red-dim);
}
</style>
