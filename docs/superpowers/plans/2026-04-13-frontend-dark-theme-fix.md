# Frontend Dark Theme Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix dark theme adaptation for AppSidebar, HomeView, UploadPanel, and ProjectView components so all components properly respond to theme changes.

**Architecture:** Each component will be updated to use CSS variables defined in dark.css/light.css theme files instead of hardcoded colors. The AppSidebar will dynamically bind to the current theme instead of hardcoding `theme="light"`.

**Tech Stack:** Vue 3, CSS Variables, Ant Design Vue

---

## File Modifications Overview

| File | Changes |
|------|---------|
| `frontend/src/components/AppSidebar.vue` | Dynamic theme binding, CSS variable replacement |
| `frontend/src/views/HomeView.vue` | Modal/form CSS variable replacement |
| `frontend/src/components/UploadPanel.vue` | All hardcoded colors → CSS variables |
| `frontend/src/views/ProjectView.vue` | `var(--white)` → `var(--bg1)` |
| `frontend/src/App.vue` | Body background → `var(--bg)` |

---

## Task 1: Fix AppSidebar.vue

**Files:**
- Modify: `frontend/src/components/AppSidebar.vue`

- [ ] **Step 1: Read current AppSidebar.vue**

File: `frontend/src/components/AppSidebar.vue`
Current content:
```vue
<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { ref, watch } from 'vue'
import { FileSearchOutlined, HistoryOutlined, BookOutlined } from '@ant-design/icons-vue'

const router = useRouter()
const route = useRoute()

const menuItems = [
  { key: '/home/check', label: '标书检查', icon: FileSearchOutlined },
  { key: '/home/history', label: '历史标书', icon: HistoryOutlined },
  { key: '/home/knowledge', label: '知识库', icon: BookOutlined },
]

const selectedKeys = ref<string[]>([route.path])

watch(() => route.path, (newPath) => {
  selectedKeys.value = [newPath]
})

function navigate(path: string) {
  router.push(path)
}

function handleMenuClick(e: { key: string }) {
  navigate(e.key)
}
</script>

<template>
  <a-menu
    v-model:selectedKeys="selectedKeys"
    mode="inline"
    theme="light"
    class="app-sidebar"
    @click="handleMenuClick"
  >
    <a-menu-item v-for="item in menuItems" :key="item.key">
      <template #icon>
        <component :is="item.icon" />
      </template>
      {{ item.label }}
    </a-menu-item>
  </a-menu>
</template>

<style scoped>
.app-sidebar {
  height: 100%;
  background: #fafafa;
  border-right: 1px solid #e8e8e8;
}

.app-sidebar :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
}

.app-sidebar :deep(.ant-menu-item-selected) {
  background: linear-gradient(90deg, rgba(99, 102, 241, 0.1) 0%, transparent 100%);
  border-left: 2px solid #6366f1;
}
</style>
```

- [ ] **Step 2: Update script to import useTheme**

Add `useTheme` import and get current theme:
```typescript
import { useRouter, useRoute } from 'vue-router'
import { ref, watch, computed } from 'vue'
import { FileSearchOutlined, HistoryOutlined, BookOutlined } from '@ant-design/icons-vue'
import { useTheme } from '@/composables/useTheme'

const router = useRouter()
const route = useRoute()
const { theme } = useTheme()

const selectedKeys = ref<string[]>([route.path])
```

- [ ] **Step 3: Change theme="light" to dynamic :theme binding**

In the `<a-menu>` element:
```vue
<a-menu
  v-model:selectedKeys="selectedKeys"
  mode="inline"
  :theme="theme"
  class="app-sidebar"
  @click="handleMenuClick"
>
```

- [ ] **Step 4: Replace hardcoded CSS colors**

Replace the `<style scoped>` section:
```css
<style scoped>
.app-sidebar {
  height: 100%;
  background: var(--bg2);
  border-right: 1px solid var(--line);
}

.app-sidebar :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
}

.app-sidebar :deep(.ant-menu-item-selected) {
  background: linear-gradient(90deg, rgba(99, 102, 241, 0.1) 0%, transparent 100%);
  border-left: 2px solid var(--purple);
}
</style>
```

- [ ] **Step 5: Commit changes**

```bash
git add frontend/src/components/AppSidebar.vue
git commit -m "fix(AppSidebar): use dynamic theme binding and CSS variables

- Bind theme prop dynamically from useTheme composable
- Replace hardcoded #fafafa with var(--bg2)
- Replace hardcoded #e8e8e8 with var(--line)
- Replace hardcoded #6366f1 with var(--purple)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Fix App.vue body background

**Files:**
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Read current App.vue**

File: `frontend/src/App.vue`
Current content:
```vue
<style>
@import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&family=Poppins:wght@500;600;700&display=swap');
@import '@/assets/themes/common.css';

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f5f3ff;
  color: #1e1b4b;
  line-height: 1.6;
}
```

- [ ] **Step 2: Replace hardcoded body background with CSS variable**

Replace the `body` section in `<style>`:
```css
body {
  font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
}
```

- [ ] **Step 3: Commit changes**

```bash
git add frontend/src/App.vue
git commit -m "fix(App): use CSS variable for body background

- Replace hardcoded #f5f3ff with var(--bg)
- Replace hardcoded #1e1b4b with var(--text)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Fix HomeView.vue Modal and Form

**Files:**
- Modify: `frontend/src/views/HomeView.vue`

- [ ] **Step 1: Read current HomeView.vue**

Focus on these CSS sections in the file:
```css
.header {
  background: var(--bg1);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}
...
.modal {
  background: var(--bg1);
}
...
.form-group input,
.form-group textarea {
  border: 1px solid var(--line);
}
.form-group input:focus,
.form-group textarea:focus {
  border-color: var(--purple);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--purple) 15%, transparent);
}
```

- [ ] **Step 2: Check if existing CSS variables are sufficient**

From dark.css, the CSS variables `--bg1`, `--line`, `--purple` already exist. Verify the modal and form elements use these variables correctly. The current code already uses CSS variables in most places, but verify the `.header h1` color and `.modal-actions button[type="button"]` background.

Check and update if needed:
```css
.header h1 {
  color: var(--purple); /* Already uses CSS variable - OK */
}
...
.modal-actions button[type="button"] {
  background: var(--bg3);
  color: var(--text);  /* Already uses CSS variables - OK */
}
```

- [ ] **Step 3: Verify the modal overlay uses correct color**

The modal-overlay uses `rgba(0, 0, 0, 0.5)` which is acceptable for backdrop (intentionally dark), but could be made more theme-aware if needed. For now, leave as-is since backdrop should be dark.

- [ ] **Step 4: Commit (if changes were made, otherwise skip)**

```bash
git add frontend/src/views/HomeView.vue
git commit -m "refactor(HomeView): verify CSS variable usage in modal

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Fix UploadPanel.vue

**Files:**
- Modify: `frontend/src/components/UploadPanel.vue`

- [ ] **Step 1: Read current UploadPanel.vue**

File: `frontend/src/components/UploadPanel.vue`

Current problematic hardcoded colors:
- `.document-card { border: 2px dashed #ddd; }`
- `.document-card h3 { color: #333; }`
- `.filename { color: #333; }`
- `.doc-meta { color: #666; }`
- `.upload-label { border: 2px dashed #6366f1; color: #6366f1; }`
- `.upload-label:hover { background: #f5f3ff; }`
- `.status-pending { background: #ddd; color: #666; }`
- `.status-running { background: #f6e05e; color: #744210; }`
- `.status-success { background: #68d391; color: #22543d; }`
- `.status-error { background: #fc8181; color: #742a2a; }`
- `.view-btn { background: #6366f1; color: white; }`
- `.delete-btn { background: #e53e3e; color: white; }`

- [ ] **Step 2: Replace all hardcoded colors with CSS variables**

Replace the entire `<style scoped>` section:
```css
<style scoped>
.document-card {
  border: 2px dashed var(--line);
  border-radius: var(--r2);
  padding: 1.5rem;
  text-align: center;
  background: var(--bg1);
}

.document-card h3 {
  color: var(--text);
  margin-bottom: 1rem;
}

.document-info {
  text-align: left;
}

.doc-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.filename {
  color: var(--text);
  font-weight: 500;
  word-break: break-all;
}

.doc-meta {
  color: var(--muted);
  font-size: 0.9rem;
  margin: 0.5rem 0;
}

.upload-area {
  position: relative;
}

.file-input {
  position: absolute;
  width: 100%;
  height: 100%;
  top: 0;
  left: 0;
  opacity: 0;
  cursor: pointer;
}

.upload-label {
  display: block;
  padding: 2rem;
  border: 2px dashed var(--purple);
  border-radius: var(--r2);
  color: var(--purple);
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.upload-label:hover {
  background: var(--purple-bg);
}

.status {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 500;
}

.status-pending {
  background: var(--bg3);
  color: var(--muted);
}

.status-running {
  background: var(--amber-bg);
  color: var(--amber);
}

.status-success {
  background: var(--green-bg);
  color: var(--green);
}

.status-error {
  background: var(--red-bg);
  color: var(--red);
}

.button-group {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.view-btn,
.delete-btn {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  font-size: 0.85rem;
  flex: 1;
}

.view-btn {
  background: var(--purple);
  color: var(--white);
  transition: filter 0.2s ease;
}

.delete-btn {
  background: var(--red);
  color: var(--white);
  transition: filter 0.2s ease;
}

.view-btn:hover {
  filter: brightness(1.1);
}

.delete-btn:hover {
  filter: brightness(1.1);
}
</style>
```

- [ ] **Step 3: Commit changes**

```bash
git add frontend/src/components/UploadPanel.vue
git commit -m "fix(UploadPanel): replace all hardcoded colors with CSS variables

- border: #ddd → var(--line)
- text colors: #333/#666 → var(--text)/var(--muted)
- upload label: #6366f1 → var(--purple)
- upload hover: #f5f3ff → var(--purple-bg)
- status badges: use var(--green), var(--amber), var(--red) with their -bg variants
- buttons: use var(--purple), var(--red) with var(--white) text

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Fix ProjectView.vue

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

- [ ] **Step 1: Read current ProjectView.vue**

Search for `var(--white)` in the file:
```bash
grep -n "var(--white)" frontend/src/views/ProjectView.vue
```

Expected locations:
- `.section { background: var(--white); }`
- `.document-card { background: var(--white); }`

- [ ] **Step 2: Replace var(--white) with var(--bg1)**

Replace:
```css
.section {
  background: var(--bg1);  /* was var(--white) */
  ...
}

.document-card {
  background: var(--bg1);  /* was var(--white) */
  ...
}
```

- [ ] **Step 3: Commit changes**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "fix(ProjectView): replace var(--white) with var(--bg1)

- .section background: var(--white) → var(--bg1)
- .document-card background: var(--white) → var(--bg1)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Verification

- [ ] **Step 1: Start frontend dev server**

```bash
cd frontend && npm run dev
```

- [ ] **Step 2: Test theme switching**

1. Open http://localhost:3000 in browser
2. Open Chrome DevTools → Elements panel
3. Check `<body>` class should be `theme-dark` or `theme-light`
4. Toggle theme button and verify:
   - AppSidebar background changes between `var(--bg2)` (dark) and `var(--bg2)` equivalent in light
   - Upload labels show correct purple theme color
   - Status badges use correct colors
   - No hardcoded color values visible in computed styles

- [ ] **Step 3: Verify all CSS variables are resolved**

In DevTools, check that no hardcoded hex colors like `#ddd`, `#333`, `#6366f1` appear in styled components except in places where they're intentionally inline SVG or icons.

---

## Summary

| Task | File | Status |
|------|------|--------|
| 1 | AppSidebar.vue | Ready |
| 2 | App.vue | Ready |
| 3 | HomeView.vue | Verified (mostly already uses CSS variables) |
| 4 | UploadPanel.vue | Ready |
| 5 | ProjectView.vue | Ready |
