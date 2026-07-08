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

function goToResults(projectId: string) {
  router.push({ name: 'review-results', params: { id: projectId } })
}

async function deleteProject(projectId: string) {
  await projectStore.deleteProject(projectId)
  message.success('项目已删除')
}
</script>

<template>
  <div class="history-view">
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
            <a @click="goToResults(record.id)" class="project-link">
              {{ record.name }}
            </a>
          </template>
          <template v-else-if="column.key === 'description'">
            {{ record.description || '-' }}
          </template>
          <template v-else-if="column.key === 'created_at'">
            <span class="text-mono">{{ new Date(record.created_at).toLocaleDateString() }}</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <a-space>
              <a @click="goToResults(record.id)">审查结果</a>
              <a-divider type="vertical" />
              <a-popconfirm
                title="确定要删除此项目吗？"
                @confirm="deleteProject(record.id)"
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
.filter-card,
.list-card {
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-md);
  margin-bottom: 24px;
}

.filters {
  display: flex;
  gap: 16px;
  align-items: center;
}

.project-link {
  color: var(--blue);
  font-weight: 500;
  transition: color 0.2s ease;
}

.project-link:hover {
  opacity: 0.8;
}

.delete-link {
  color: var(--red);
  transition: color 0.2s ease;
}

.delete-link:hover {
  opacity: 0.8;
}
</style>
