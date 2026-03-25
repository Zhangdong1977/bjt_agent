<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import SummaryCard from '@/components/SummaryCard.vue'
import ResultsTable from '@/components/ResultsTable.vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const projectId = computed(() => route.params.id as string)

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  if (!projectStore.reviewResults) {
    await projectStore.fetchReviewResults()
  }
})

function goBack() {
  router.push({ name: 'project', params: { id: projectId.value } })
}

function goToTimeline() {
  router.push({ name: 'review-timeline', params: { id: projectId.value } })
}
</script>

<template>
  <div class="results-view">
    <header class="header">
      <div class="header-left">
        <button @click="goBack" class="back-btn">← Back</button>
        <h1>Review Results: {{ projectStore.currentProject?.name }}</h1>
      </div>
      <button
        v-if="projectStore.currentTask"
        @click="goToTimeline"
        class="timeline-btn"
      >
        View Timeline
      </button>
    </header>

    <main class="content">
      <section v-if="projectStore.reviewResults" class="section">
        <h2>Summary</h2>
        <SummaryCard :summary="projectStore.reviewResults.summary" />

        <h2>Findings</h2>
        <ResultsTable :findings="projectStore.reviewResults.findings" />
      </section>

      <div v-else class="no-results">
        <p>No review results available.</p>
        <p>Please run a review first from the project page.</p>
      </div>
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

.timeline-btn {
  padding: 0.5rem 1rem;
  background: #667eea;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.timeline-btn:hover {
  background: #5568d3;
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
  margin-bottom: 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid #667eea;
}

.no-results {
  text-align: center;
  padding: 3rem;
  color: #666;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.no-results p {
  margin: 0.5rem 0;
}
</style>
