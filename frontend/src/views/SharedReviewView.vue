<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { shareApi, type SharedReview } from '@/api/client'
import ReviewResultsArea from '@/components/ReviewResultsArea.vue'
import type { ReviewResponse } from '@/types'

const route = useRoute()
const router = useRouter()

const token = computed(() => route.params.token as string)
const loading = ref(true)
const errorMsg = ref('')
const shared = ref<SharedReview | null>(null)

const taskResults = computed<ReviewResponse | null>(() => {
  const s = shared.value
  if (!s) return null
  const nonCompliant = s.findings.filter((f) => !f.is_compliant)
  return {
    summary: {
      category_count: s.todos.length,
      check_item_count: s.todos.reduce(
        (sum, t) => sum + ((t as any).check_items?.length || 0),
        0,
      ),
      risk_item_count: new Set(
        nonCompliant.map((f) => f.check_item_name).filter(Boolean),
      ).size,
    },
    findings: s.findings,
  }
})

// 分享页的报告获取走 share token（非项目所有者，不能用默认 ownership 接口）。
function reportFetcher(todoId: string): Promise<string> {
  return shareApi.getSharedReport(token.value, todoId)
}

onMounted(async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    shared.value = await shareApi.getSharedReview(token.value)
  } catch (e: any) {
    errorMsg.value =
      e?.response?.data?.detail || '加载分享结果失败，链接可能已失效或被撤销'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="shared-view">
    <main class="content">
      <div class="header">
        <h2 class="title">
          {{ shared?.project_name ? `${shared.project_name} - 审查结果` : '分享的审查结果' }}
        </h2>
      </div>

      <a-spin :spinning="loading">
        <div v-if="errorMsg" class="error-box">
          <a-alert type="error" show-icon :message="errorMsg" />
          <div class="back-row">
            <a-button type="primary" @click="router.push('/home/check')">返回首页</a-button>
          </div>
        </div>

        <section v-else-if="taskResults" class="section">
          <ReviewResultsArea
            :review-results="taskResults"
            :todos="shared!.todos"
            :project-id="shared!.project_id"
            :task-id="shared!.task_id"
            :report-fetcher="reportFetcher"
          />
          <a-alert
            class="disclaimer-alert"
            type="warning"
            show-icon
            message="检查结果由大模型生成，仅供参考，请谨慎判别！"
            description="本结果不可作为最终判定是否废标的依据，最终结果以专家实际判别为准。"
          />
        </section>
      </a-spin>
    </main>
  </div>
</template>

<style scoped>
.shared-view {
  min-height: 100vh;
  background: #f5f7fa;
}

.content {
  max-width: 1200px;
  margin: 0 auto;
  padding: 1.5rem 1rem;
}

.header {
  margin-bottom: 1rem;
}

.title {
  color: #222;
  font-size: 20px;
  font-weight: 700;
}

.section {
  background: var(--bg1, #fff);
  padding: 1.5rem;
  border-radius: var(--r-lg, 12px);
  border: 1px solid var(--line, #eee);
}

.disclaimer-alert {
  margin-top: 1.25rem;
}

.error-box {
  padding: 2rem;
  background: #fff;
  border-radius: 12px;
  border: 1px solid var(--line, #eee);
}

.back-row {
  margin-top: 1rem;
  text-align: center;
}

@media (max-width: 767px) {
  .content {
    padding: 1rem;
  }

  .section {
    padding: 1rem;
  }
}
</style>
