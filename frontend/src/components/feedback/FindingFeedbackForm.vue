<script setup lang="ts">
import { ref, computed } from 'vue'
import type { FeedbackCreateRequest } from '@/types'

const props = defineProps<{
  feedbackType: 'confirm' | 'contradict' | 'refine'
  submitting: boolean
}>()

const emit = defineEmits<{
  (e: 'submit', data: FeedbackCreateRequest): void
  (e: 'cancel'): void
}>()

const comment = ref('')
const contradictReason = ref<'should_comply' | 'severity_too_high' | 'severity_too_low' | 'item_not_applicable' | ''>('')
const correctedSeverity = ref<'critical' | 'major' | 'minor' | ''>('')
const correctedSuggestion = ref('')
const correctedIsCompliant = ref<boolean | undefined>(undefined)

const contradictReasonOptions = [
  { value: 'should_comply', label: '该项实际合规' },
  { value: 'severity_too_high', label: '严重程度过高' },
  { value: 'severity_too_low', label: '严重程度过低' },
  { value: 'item_not_applicable', label: '检查项不适用' },
]

const severityOptions = [
  { value: 'critical', label: '严重' },
  { value: 'major', label: '重要' },
  { value: 'minor', label: '一般' },
]

const canSubmit = computed(() => {
  if (props.feedbackType === 'contradict') {
    return contradictReason.value !== ''
  }
  if (props.feedbackType === 'refine') {
    return correctedSeverity.value !== '' || correctedSuggestion.value.trim() !== '' || correctedIsCompliant.value !== undefined
  }
  return true  // confirm always valid
})

function onSubmit() {
  if (!canSubmit.value || props.submitting) return

  const data: FeedbackCreateRequest = {
    feedback_type: props.feedbackType,
    comment: comment.value.trim() || undefined,
  }

  if (props.feedbackType === 'contradict') {
    data.contradict_reason = contradictReason.value || undefined
  }

  if (props.feedbackType === 'refine') {
    data.corrected_severity = correctedSeverity.value || undefined
    data.corrected_suggestion = correctedSuggestion.value.trim() || undefined
    data.corrected_is_compliant = correctedIsCompliant.value
  }

  emit('submit', data)
}
</script>

<template>
  <div class="feedback-form">
    <!-- Contradict: reason selector -->
    <div v-if="feedbackType === 'contradict'" class="form-field">
      <label class="form-label">反对原因 <span class="required">*</span></label>
      <select v-model="contradictReason" class="form-select">
        <option value="" disabled>请选择原因</option>
        <option v-for="opt in contradictReasonOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </div>

    <!-- Refine: correction fields -->
    <template v-if="feedbackType === 'refine'">
      <div class="form-field">
        <label class="form-label">修正严重程度</label>
        <select v-model="correctedSeverity" class="form-select">
          <option value="">不修改</option>
          <option v-for="opt in severityOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </div>
      <div class="form-field">
        <label class="form-label">修正建议</label>
        <textarea
          v-model="correctedSuggestion"
          class="form-textarea"
          placeholder="输入修正后的建议（可选）"
          rows="2"
        ></textarea>
      </div>
      <div class="form-field">
        <label class="form-checkbox">
          <input type="checkbox" v-model="correctedIsCompliant" :true-value="true" :false-value="undefined" />
          <span>标记为合规</span>
        </label>
      </div>
    </template>

    <!-- Common comment -->
    <div class="form-field">
      <label class="form-label">补充说明</label>
      <textarea
        v-model="comment"
        class="form-textarea"
        placeholder="可选，输入补充说明..."
        rows="2"
      ></textarea>
    </div>

    <!-- Actions -->
    <div class="form-actions">
      <button
        class="submit-btn"
        :disabled="!canSubmit || submitting"
        @click="onSubmit"
      >
        {{ submitting ? '提交中...' : '提交' }}
      </button>
      <button class="cancel-btn" @click="emit('cancel')">取消</button>
    </div>
  </div>
</template>

<style scoped>
.feedback-form {
  margin-top: 8px;
  padding: 10px 12px;
  background: var(--bg2);
  border-radius: var(--r);
  border: 1px solid var(--blue-dim);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--sub);
}

.required {
  color: var(--red);
}

.form-select,
.form-textarea {
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--bg1);
  color: var(--text);
  font-size: 12px;
  font-family: inherit;
}

.form-select:focus,
.form-textarea:focus {
  outline: none;
  border-color: var(--blue);
}

.form-textarea {
  resize: vertical;
  min-height: 36px;
}

.form-checkbox {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text);
  cursor: pointer;
}

.form-checkbox input[type="checkbox"] {
  accent-color: var(--blue);
}

.form-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}

.submit-btn {
  padding: 4px 16px;
  background: var(--blue);
  color: var(--white);
  border: none;
  border-radius: var(--r);
  font-size: 12px;
  cursor: pointer;
  font-weight: 500;
}

.submit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cancel-btn {
  padding: 4px 12px;
  background: transparent;
  color: var(--muted);
  border: 1px solid var(--line);
  border-radius: var(--r);
  font-size: 12px;
  cursor: pointer;
}

.cancel-btn:hover {
  border-color: var(--muted);
}
</style>
