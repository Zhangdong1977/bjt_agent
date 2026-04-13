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
        <button @click="goBack" class="back-btn">← 返回</button>
        <h1>审查时间线: {{ projectStore.currentProject?.name }}</h1>
      </div>
    </header>

    <main class="content">
      <section class="section">
        <h2>智能体执行时间线</h2>

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
          暂无进行中的审查任务。请从项目页面开始审查。
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
  background: var(--bg1);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.back-btn {
  padding: 0.5rem 1rem;
  background: var(--bg3);
  color: var(--text);
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.header h1 {
  color: var(--text);
  font-size: 1.5rem;
}

.content {
  max-width: 1200px;
  margin: 2rem auto;
  padding: 0 2rem;
}

.section {
  background: var(--bg1);
  padding: 1.5rem;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.section h2 {
  color: var(--text);
  margin-bottom: 1.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--purple);
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
  background: var(--bg4);
  color: var(--muted);
}

.status-running {
  background: var(--amber-bg);
  color: var(--amber);
}

.status-completed {
  background: var(--green-bg);
  color: var(--green);
}

.status-failed {
  background: var(--red-bg);
  color: var(--red);
}

.error-msg {
  color: var(--red);
  font-size: 0.9rem;
}

.no-task {
  text-align: center;
  padding: 3rem;
  color: var(--sub);
}
</style>
