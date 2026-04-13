# 前端样式统一与主题切换实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将所有页面硬编码样式统一为 CSS 变量系统，实现亮/暗主题切换和响应式布局

**Architecture:**
- 创建 `common.css` 全局样式层定义通用样式类
- 创建 `ThemeToggle.vue` 组件实现主题切换按钮
- 重构各页面使用 CSS 变量替代硬编码颜色
- 添加 `@media` 断点实现响应式布局

**Tech Stack:** Vue 3, CSS Variables, TypeScript, Composition API

---

## Phase 1: 基础设施

### Task 1: 创建 common.css 全局样式层

**Files:**
- Create: `frontend/src/assets/themes/common.css`

- [ ] **Step 1: 创建 common.css 文件**

```css
/* ========================================
   Global Styles - CSS Variable Based
   ======================================== */

/* 通用容器 */
.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 2rem;
}

/* 通用卡片 */
.card {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  padding: 1rem;
  transition: box-shadow 0.2s ease;
}

.card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}

/* 通用按钮 */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.5rem 1rem;
  border: none;
  border-radius: var(--r);
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: all 0.2s ease;
}

.btn-primary {
  background: var(--purple);
  color: var(--white);
}

.btn-primary:hover {
  filter: brightness(1.1);
}

.btn-secondary {
  background: var(--bg3);
  color: var(--text);
  border: 1px solid var(--line);
}

.btn-secondary:hover {
  background: var(--bg4);
}

/* 状态标签 */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.6rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 600;
  border: 1px solid transparent;
}

.badge-success {
  background: var(--green-bg);
  color: var(--green);
  border-color: var(--green-dim);
}

.badge-warning {
  background: var(--amber-bg);
  color: var(--amber);
  border-color: var(--amber-dim);
}

.badge-error {
  background: var(--red-bg);
  color: var(--red);
  border-color: var(--red-dim);
}

.badge-info {
  background: var(--blue-bg);
  color: var(--blue);
  border-color: var(--blue-dim);
}

/* Section */
.section {
  background: var(--bg1);
  border: 1px solid var(--line);
  border-radius: var(--r2);
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}

.section-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--purple);
  margin-bottom: 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--purple);
}

/* 响应式网格 */
.grid-responsive {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1.5rem;
}

/* Header */
.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 2rem;
  background: var(--bg1);
  border-bottom: 1px solid var(--line);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 1rem;
}

/* 响应式断点 */
@media (max-width: 1199px) {
  .container {
    padding: 0 1.5rem;
  }

  .header {
    padding: 0.75rem 1.5rem;
  }
}

@media (max-width: 767px) {
  .container {
    padding: 0 1rem;
  }

  .grid-responsive {
    grid-template-columns: 1fr;
  }

  .header {
    padding: 0.75rem 1rem;
  }

  .section {
    padding: 1rem;
  }
}
```

- [ ] **Step 2: 在 App.vue 中导入 common.css**

修改 `frontend/src/App.vue`:
```vue
<style>
@import url('./assets/themes/common.css');
/* 其他现有样式保持不变 */
</style>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/assets/themes/common.css frontend/src/App.vue
git commit -m "feat: add common.css global styles layer

Add global style classes using CSS variables:
- Container, card, button, badge components
- Responsive breakpoints at 1199px and 767px
- Section styling with purple accent

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: 创建 ThemeToggle 组件

**Files:**
- Create: `frontend/src/components/ThemeToggle.vue`

- [ ] **Step 1: 创建 ThemeToggle.vue 组件**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'

const { theme, toggleTheme } = useTheme()

const icon = computed(() => theme.value === 'dark' ? '☀️' : '🌙')
const label = computed(() => theme.value === 'dark' ? '切换亮色模式' : '切换暗色模式')
</script>

<template>
  <button
    class="theme-toggle"
    @click="toggleTheme"
    :title="label"
    :aria-label="label"
  >
    <span class="theme-icon">{{ icon }}</span>
  </button>
</template>

<style scoped>
.theme-toggle {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: var(--bg2);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  color: var(--text);
  font-size: 16px;
}

.theme-toggle:hover {
  background: var(--bg3);
  border-color: var(--purple);
  transform: scale(1.05);
}

.theme-toggle:active {
  transform: scale(0.95);
}

.theme-icon {
  line-height: 1;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ThemeToggle.vue
git commit -m "feat: add ThemeToggle component

Add theme toggle button with sun/moon icons.
Integrates with useTheme composable for theme switching.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: 重构 AppLayout Header

**Files:**
- Modify: `frontend/src/components/AppLayout.vue`

- [ ] **Step 1: 读取现有 AppLayout.vue**

读取当前 `AppLayout.vue` 内容，了解其结构。

- [ ] **Step 2: 添加 ThemeToggle 到 Header**

在 header-right 中添加 ThemeToggle 组件引用。

- [ ] **Step 3: 更新 Header 样式使用 CSS 变量**

将硬编码颜色替换为 CSS 变量。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AppLayout.vue
git commit -m "refactor: update AppLayout header with ThemeToggle

- Add ThemeToggle component to header-right
- Replace hardcoded colors with CSS variables
- Add responsive header styles

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Phase 2: 页面重构

### Task 4: 重构 HomeView.vue

**Files:**
- Modify: `frontend/src/views/HomeView.vue`

- [ ] **Step 1: 读取现有 HomeView.vue**

读取完整内容，识别所有硬编码颜色。

- [ ] **Step 2: 替换 Header 样式**

```css
/* 旧 */
.header {
  background: white;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}
.header h1 { color: #6366f1; }

/* 新 */
.header {
  background: var(--bg1);
  border-bottom: 1px solid var(--line);
}
.header h1 { color: var(--purple); }
```

- [ ] **Step 3: 替换按钮样式**

```css
/* 旧 */
.primary-btn { background: #6366f1; }
.primary-btn:hover { background: #4f46e5; }
.logout-btn { background: #e53e3e; }

/* 新 */
.primary-btn { background: var(--purple); }
.primary-btn:hover { background: var(--purple); filter: brightness(1.1); }
.logout-btn { background: var(--red); }
```

- [ ] **Step 4: 替换卡片和表单样式**

将 `#333`, `#666`, `#ddd` 等替换为 CSS 变量。

- [ ] **Step 5: 添加响应式样式**

在文件末尾添加 `@media` 断点样式。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/HomeView.vue
git commit -m "refactor: unify HomeView styles with CSS variables

- Replace hardcoded colors with CSS variables
- Add responsive breakpoints at 1199px and 767px
- Use common.css utility classes where applicable

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: 重构 ProjectView.vue

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

- [ ] **Step 1: 读取现有 ProjectView.vue**

- [ ] **Step 2: 替换硬编码颜色**

颜色映射表：
- `#6366f1` → `var(--purple)`
- `#4f46e5` → `var(--purple)` (hover)
- `#1e1b4b` → `var(--text)`
- `#333` → `var(--text)`
- `#ddd` → `var(--line)`
- `#e5e7eb` → `var(--line)`

- [ ] **Step 3: 替换 status 样式**

```css
/* 旧 */
.status-pending { background: #f3f4f6; color: #6b7280; }
.status-running { background: #fef9c3; color: #854d0e; }
.status-success { background: #dcfce7; color: #166534; }
.status-error { background: #fee2e2; color: #991b1b; }

/* 新 - 使用 common.css 的 badge 类 */
```

- [ ] **Step 4: 添加响应式网格**

修改 `.documents-grid` 使用 `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "refactor: unify ProjectView styles with CSS variables

- Replace hardcoded colors with CSS variables
- Add responsive grid for documents section
- Use badge classes from common.css

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: 重构 ResultsView.vue

**Files:**
- Modify: `frontend/src/views/ResultsView.vue`

- [ ] **Step 1: 读取现有 ResultsView.vue**

- [ ] **Step 2: 替换硬编码颜色**

- `#667eea` → `var(--purple)`
- `#333` → `var(--text)`
- `#ddd` → `var(--line)`
- `#fff` → `var(--white)`

- [ ] **Step 3: 使用 CSS 变量统一**

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/ResultsView.vue
git commit -m "refactor: unify ResultsView styles with CSS variables

- Replace hardcoded colors with CSS variables
- Add responsive section padding

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: 重构 KnowledgeView.vue

**Files:**
- Modify: `frontend/src/views/KnowledgeView.vue`

- [ ] **Step 1: 读取现有 KnowledgeView.vue**

- [ ] **Step 2: 替换硬编码颜色**

颜色映射：
- `#6366f1` → `var(--purple)`
- `#333` → `var(--text)`

- [ ] **Step 3: 添加响应式侧边栏**

添加 `@media (max-width: 767px)` 隐藏侧边栏的样式。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/KnowledgeView.vue
git commit -m "refactor: unify KnowledgeView styles with CSS variables

- Replace hardcoded colors with CSS variables
- Add responsive sidebar hiding on mobile

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Phase 3: 收尾

### Task 8: 增强 useTheme 支持系统偏好

**Files:**
- Modify: `frontend/src/composables/useTheme.ts`

- [ ] **Step 1: 读取现有 useTheme.ts**

- [ ] **Step 2: 添加系统偏好监听**

在 `initTheme()` 函数中添加：

```typescript
// 检查系统偏好
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)')

function handleSystemChange(e: MediaQueryListEvent) {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (!stored) {
    theme.value = e.matches ? 'dark' : 'light'
    applyTheme(theme.value)
  }
}

prefersDark.addEventListener('change', handleSystemChange)
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useTheme.ts
git commit -m "feat: add system theme preference detection

- Listen to prefers-color-scheme media query
- Auto-detect system dark/light mode on first visit
- User preference stored in localStorage overrides system

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 9: 集成测试

**Files:**
- 测试目标：所有页面

- [ ] **Step 1: 启动开发服务器**

```bash
cd frontend && npm run dev
```

- [ ] **Step 2: 测试主题切换**

1. 打开浏览器控制台
2. 点击 Header 右上角主题切换按钮
3. 验证背景色、文字颜色是否正确切换到亮/暗主题

- [ ] **Step 3: 测试响应式布局**

1. 打开浏览器开发者工具
2. 使用 Device Toolbar (Ctrl+Shift+M)
3. 测试三个断点：桌面(1200px+)、平板(768-1199px)、手机(<768px)
4. 验证布局是否正确自适应

- [ ] **Step 4: 测试页面一致性**

访问各页面（Home、Project、Results、Knowledge），验证：
- 颜色风格一致
- 按钮、卡片样式统一
- 无硬编码颜色泄漏

---

## 总结

| Task | 组件/页面 | 状态 |
|------|-----------|------|
| 1 | common.css | ⬜ |
| 2 | ThemeToggle.vue | ⬜ |
| 3 | AppLayout.vue | ⬜ |
| 4 | HomeView.vue | ⬜ |
| 5 | ProjectView.vue | ⬜ |
| 6 | ResultsView.vue | ⬜ |
| 7 | KnowledgeView.vue | ⬜ |
| 8 | useTheme.ts | ⬜ |
| 9 | 集成测试 | ⬜ |

---

## 附录：CSS 变量参考

| 变量 | 浅色值 | 暗色值 | 用途 |
|------|--------|--------|------|
| `--bg` | #f5f5f5 | #0a0a0a | 页面背景 |
| `--bg1` | #ffffff | #111111 | 卡片背景 |
| `--text` | #333333 | #cccccc | 正文文字 |
| `--purple` | #722ed1 | #a78bfa | 主色调 |
| `--line` | #e0e0e0 | #2a2a2a | 边框线 |
| `--muted` | #888888 | #666666 | 次要文字 |
