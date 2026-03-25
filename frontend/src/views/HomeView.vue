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
  if (confirm('Are you sure you want to delete this project?')) {
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
      <h1>Bid Review Agent</h1>
      <div class="user-info">
        <span>{{ authStore.user?.username }}</span>
        <button @click="logout" class="logout-btn">Logout</button>
      </div>
    </header>

    <main class="content">
      <div class="projects-header">
        <h2>My Projects</h2>
        <button @click="showCreateModal = true" class="primary-btn">
          New Project
        </button>
      </div>

      <div v-if="projectStore.loading" class="loading">Loading...</div>

      <div v-else-if="projectStore.projects.length === 0" class="empty-state">
        <p>No projects yet. Create your first project to get started.</p>
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
              Created {{ new Date(project.created_at).toLocaleDateString() }}
            </span>
          </div>
          <button
            class="delete-btn"
            @click="deleteProject(project.id, $event)"
          >
            Delete
          </button>
        </div>
      </div>
    </main>

    <!-- Create Project Modal -->
    <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
      <div class="modal">
        <h3>Create New Project</h3>
        <form @submit.prevent="createProject">
          <div class="form-group">
            <label for="projectName">Project Name</label>
            <input
              id="projectName"
              v-model="newProjectName"
              type="text"
              required
              placeholder="e.g., Project Alpha"
            />
          </div>
          <div class="form-group">
            <label for="projectDesc">Description (optional)</label>
            <textarea
              id="projectDesc"
              v-model="newProjectDescription"
              placeholder="Brief description of the project"
              rows="3"
            ></textarea>
          </div>
          <div class="modal-actions">
            <button type="button" @click="showCreateModal = false">Cancel</button>
            <button type="submit" class="primary-btn">Create</button>
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
  background: white;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.header h1 {
  color: #667eea;
  font-size: 1.5rem;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.logout-btn {
  padding: 0.5rem 1rem;
  background: #e53e3e;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
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
  color: #333;
}

.primary-btn {
  padding: 0.75rem 1.5rem;
  background: #667eea;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.primary-btn:hover {
  background: #5568d3;
}

.loading, .empty-state {
  text-align: center;
  padding: 3rem;
  color: #666;
}

.projects-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1.5rem;
}

.project-card {
  background: white;
  padding: 1.5rem;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  cursor: pointer;
  transition: box-shadow 0.2s;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.project-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.project-info h3 {
  color: #333;
  margin-bottom: 0.5rem;
}

.project-info p {
  color: #666;
  font-size: 0.9rem;
  margin-bottom: 0.5rem;
}

.project-date {
  color: #999;
  font-size: 0.8rem;
}

.delete-btn {
  padding: 0.5rem 1rem;
  background: #e53e3e;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
}

.delete-btn:hover {
  background: #c53030;
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
  background: white;
  padding: 2rem;
  border-radius: 8px;
  width: 100%;
  max-width: 500px;
}

.modal h3 {
  margin-bottom: 1.5rem;
  color: #333;
}

.form-group {
  margin-bottom: 1rem;
}

.form-group label {
  display: block;
  margin-bottom: 0.5rem;
  color: #555;
}

.form-group input,
.form-group textarea {
  width: 100%;
  padding: 0.75rem;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 1rem;
}

.form-group input:focus,
.form-group textarea:focus {
  outline: none;
  border-color: #667eea;
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
  border-radius: 4px;
  cursor: pointer;
}

.modal-actions button[type="button"] {
  background: #ddd;
  color: #333;
}

.modal-actions .primary-btn {
  background: #667eea;
  color: white;
}
</style>
