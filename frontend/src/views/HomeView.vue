<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'

const router = useRouter()
const authStore = useAuthStore()
const projectStore = useProjectStore()

const showCreateModal = ref(false)
const newProjectName = ref('')
const newProjectDescription = ref('')

onMounted(() => {
  projectStore.fetchProjects()
})

function openProject(projectId: string) {
  router.push({ name: 'project', params: { id: projectId } })
}

async function createProject() {
  if (!newProjectName.value.trim()) return
  const project = await projectStore.createProject(
    newProjectName.value,
    newProjectDescription.value || undefined
  )
  showCreateModal.value = false
  newProjectName.value = ''
  newProjectDescription.value = ''
  if (project) {
    openProject(project.id)
  }
}

async function deleteProject(projectId: string, event: Event) {
  event.stopPropagation()
  if (confirm('确定要删除此项目吗？')) {
    await projectStore.deleteProject(projectId)
  }
}

function logout() {
  authStore.logout()
  projectStore.$reset()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="home">
    <header class="header">
      <h1>标书审查智能体</h1>
      <div class="user-info">
        <span>{{ authStore.user?.username }}</span>
        <button @click="logout" class="logout-btn">退出</button>
      </div>
    </header>

    <main class="content">
      <div class="projects-header">
        <h2>我的项目</h2>
        <button @click="showCreateModal = true" class="primary-btn">
          新建项目
        </button>
      </div>

      <div v-if="projectStore.loading" class="loading">加载中...</div>

      <div v-else-if="projectStore.projects.length === 0" class="empty-state">
        <p>暂无项目。创建您的第一个项目开始吧。</p>
      </div>

      <div v-else class="projects-grid">
        <div
          v-for="project in projectStore.projects"
          :key="project.id"
          class="project-card"
          @click="openProject(project.id)"
        >
          <div class="project-info">
            <h3>{{ project.name }}</h3>
            <p v-if="project.description">{{ project.description }}</p>
            <span class="project-date">
              创建于 {{ new Date(project.created_at).toLocaleDateString() }}
            </span>
          </div>
          <button
            class="delete-btn"
            @click="deleteProject(project.id, $event)"
          >
            删除
          </button>
        </div>
      </div>
    </main>

    <!-- Create Project Modal -->
    <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
      <div class="modal">
        <h3>创建新项目</h3>
        <form @submit.prevent="createProject">
          <div class="form-group">
            <label for="projectName">项目名称</label>
            <input
              id="projectName"
              v-model="newProjectName"
              type="text"
              required
              placeholder="例如：项目A"
            />
          </div>
          <div class="form-group">
            <label for="projectDesc">描述（可选）</label>
            <textarea
              id="projectDesc"
              v-model="newProjectDescription"
              placeholder="项目简要描述"
              rows="3"
            ></textarea>
          </div>
          <div class="modal-actions">
            <button type="button" @click="showCreateModal = false">取消</button>
            <button type="submit" class="primary-btn">创建</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home {
  min-height: 100vh;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 2rem;
  background: var(--bg1);
  border-bottom: 1px solid var(--line);
}

.header h1 {
  color: var(--blue);
  font-size: 1.5rem;
  font-weight: 600;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.logout-btn {
  padding: 0.5rem 1rem;
  background: var(--red);
  color: var(--white);
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  transition: filter 0.2s ease;
}

.logout-btn:hover {
  filter: brightness(1.1);
}

.content {
  max-width: 1200px;
  margin: 2rem auto;
  padding: 0 2rem;
}

.projects-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2rem;
}

.projects-header h2 {
  color: var(--text);
}

.primary-btn {
  padding: 0.75rem 1.5rem;
  background: var(--blue);
  color: var(--white);
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  font-weight: 500;
  transition: filter 0.2s ease, transform 0.1s ease;
}

.primary-btn:hover {
  filter: brightness(1.1);
}

.primary-btn:active {
  transform: scale(0.98);
}

.loading, .empty-state {
  text-align: center;
  padding: 3rem;
  color: var(--sub);
}

.projects-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1.5rem;
}

.project-card {
  background: var(--bg1);
  padding: 1.5rem;
  border-radius: var(--r2);
  border: 1px solid var(--line);
  cursor: pointer;
  transition: box-shadow 0.2s ease, transform 0.2s ease;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.project-card:hover {
  box-shadow: 0 4px 12px color-mix(in srgb, var(--blue) 15%, transparent);
  transform: translateY(-2px);
}

.project-info h3 {
  color: var(--text);
  margin-bottom: 0.5rem;
}

.project-info p {
  color: var(--sub);
  font-size: 0.9rem;
  margin-bottom: 0.5rem;
}

.project-date {
  color: var(--muted);
  font-size: 0.8rem;
}

.delete-btn {
  padding: 0.5rem 1rem;
  background: var(--red);
  color: var(--white);
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  font-size: 0.85rem;
  transition: filter 0.2s ease;
}

.delete-btn:hover {
  filter: brightness(1.1);
}

/* Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
}

.modal {
  background: var(--bg1);
  padding: 2rem;
  border-radius: var(--r2);
  border: 1px solid var(--line);
  width: 100%;
  max-width: 500px;
}

.modal h3 {
  margin-bottom: 1.5rem;
  color: var(--text);
}

.form-group {
  margin-bottom: 1rem;
}

.form-group label {
  display: block;
  margin-bottom: 0.5rem;
  color: var(--sub);
}

.form-group input,
.form-group textarea {
  width: 100%;
  padding: 0.75rem;
  border: 1px solid var(--line);
  border-radius: var(--r);
  font-size: 1rem;
  background: var(--bg2);
  color: var(--text);
}

.form-group input:focus,
.form-group textarea:focus {
  outline: none;
  border-color: var(--blue);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--blue) 15%, transparent);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
  margin-top: 1.5rem;
}

.modal-actions button {
  padding: 0.75rem 1.5rem;
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  transition: filter 0.2s ease;
}

.modal-actions button[type="button"] {
  background: var(--bg3);
  color: var(--text);
}

.modal-actions .primary-btn {
  background: var(--blue);
  color: var(--white);
}

.modal-actions .primary-btn:hover {
  filter: brightness(1.1);
}

@media (max-width: 767px) {
  .projects-grid {
    grid-template-columns: 1fr;
  }

  .header {
    padding: 0.75rem 1rem;
  }

  .content {
    padding: 0 1rem;
  }
}
</style>
