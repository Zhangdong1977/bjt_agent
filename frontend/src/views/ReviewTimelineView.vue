<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import ReviewTimeline from '@/components/ReviewTimeline.vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)
const timelineRef = ref<InstanceType<typeof ReviewTimeline> | null>(null)

onMounted(async () => {
  await projectStore.selectProject(projectId.value)

  if (projectStore.currentTask?.id) {
    timelineRef.value?.connect(projectStore.currentTask.id)
  }
})

onUnmounted(() => {
  timelineRef.value?.disconnect()
})

function goBack() {
  router.push({ name: 'project', params: { id: projectId.value } })
}
</script>

<template>
  <div class="review-timeline-view">
    <header class="header">
      <div class="header-left">
        <button @click="goBack" class="back-btn">← Back</button>
        <h1>Review Timeline: {{ projectStore.currentProject?.name }}</h1>
      </div>
    </header>

    <main class="content">
      <section class="section">
        <h2>Agent Execution Timeline</h2>

        <div v-if="projectStore.currentTask" class="task-info">
          <span :class="['status', `status-${projectStore.currentTask.status}`]">
            {{ projectStore.currentTask.status }}
          </span>
          <span v-if="projectStore.currentTask.error_message" class="error-msg">
            {{ projectStore.currentTask.error_message }}
          </span>
        </div>

        <ReviewTimeline
          v-if="projectStore.currentTask?.id"
          ref="timelineRef"
          :task-id="projectStore.currentTask.id"
        />

        <div v-else class="no-task">
          No active review task. Start a review from the project page.
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 2rem;
  background: white;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.back-btn {
  padding: 0.5rem 1rem;
  background: #ddd;
  color: #333;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.header h1 {
  color: #333;
  font-size: 1.5rem;
}

.content {
  max-width: 1200px;
  margin: 2rem auto;
  padding: 0 2rem;
}

.section {
  background: white;
  padding: 1.5rem;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.section h2 {
  color: #333;
  margin-bottom: 1.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid #667eea;
}

.task-info {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}

.status {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
}

.status-pending {
  background: #ddd;
  color: #666;
}

.status-running {
  background: #f6e05e;
  color: #744210;
}

.status-completed {
  background: #68d391;
  color: #22543d;
}

.status-failed {
  background: #fc8181;
  color: #742a2a;
}

.error-msg {
  color: #e53e3e;
  font-size: 0.9rem;
}

.no-task {
  text-align: center;
  padding: 3rem;
  color: #666;
}
</style>
