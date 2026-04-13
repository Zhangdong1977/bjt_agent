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
        <button @click="goBack" class="back-btn">← 返回</button>
        <h1>审查结果: {{ projectStore.currentProject?.name }}</h1>
      </div>
      <button
        v-if="projectStore.currentTask"
        @click="goToTimeline"
        class="timeline-btn"
      >
        查看时间线
      </button>
    </header>

    <main class="content">
      <section v-if="projectStore.reviewResults" class="section">
        <h2>摘要</h2>
        <SummaryCard :summary="projectStore.reviewResults.summary" />

        <h2>发现的问题</h2>
        <ResultsTable :findings="projectStore.reviewResults.findings" />
      </section>

      <div v-else class="no-results">
        <p>暂无审查结果。</p>
        <p>请从项目页面先运行审查。</p>
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
  background: var(--bg1);
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
  border-radius: var(--r);
  cursor: pointer;
}

.header h1 {
  color: var(--text);
  font-size: 1.5rem;
}

.timeline-btn {
  padding: 0.5rem 1rem;
  background: var(--purple);
  color: var(--white);
  border: none;
  border-radius: var(--r);
  cursor: pointer;
}

.timeline-btn:hover {
  filter: brightness(1.1);
}

.content {
  max-width: 1200px;
  margin: 2rem auto;
  padding: 0 2rem;
}

.section {
  background: var(--bg1);
  padding: 1.5rem;
  border-radius: var(--r2);
}

.section h2 {
  color: var(--text);
  margin-bottom: 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--purple);
}

.no-results {
  text-align: center;
  padding: 3rem;
  color: var(--sub);
  background: var(--bg1);
  border-radius: var(--r2);
}

.no-results p {
  margin: 0.5rem 0;
}

@media (max-width: 767px) {
  .header {
    padding: 0.75rem 1rem;
  }

  .content {
    padding: 0 1rem;
  }

  .section {
    padding: 1rem;
  }
}
</style>
