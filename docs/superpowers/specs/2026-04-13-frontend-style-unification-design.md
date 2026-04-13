# 前端样式统一与主题切换设计方案

**日期**: 2026-04-13
**状态**: 已批准
**目标**: 统一整个系统的页面风格，支持亮/暗主题切换和响应式布局

---

## 1. 概述与目标

将现有各页面的硬编码样式统一为 CSS 变量系统，实现：
- 全系统一致的视觉风格
- 亮/暗主题无缝切换
- 响应式布局支持多终端

## 2. 整体架构

### 1.1 样式系统

```
frontend/src/assets/themes/
├── light.css      # 亮主题变量（已存在）
├── dark.css       # 暗主题变量（已存在）
└── common.css     # 新增：全局共享样式

CSS 变量分类：
├── 背景色系 --bg, --bg1, --bg2, --bg3, --bg4
├── 边框线系 --line, --line2, --dim
├── 文字色系 --muted, --sub, --text, --bright, --white
└── 状态色系 --green, --amber, --blue, --purple, --red, --teal
```

### 1.2 组件结构

```
AppLayout (全局布局)
├── AppHeader
│   ├── Logo / 标题
│   ├── 导航菜单
│   └── ThemeToggle (主题切换按钮) ← 新增
├── AppSidebar (侧边栏，响应式显示/隐藏)
└── RouterView (页面内容)
```

### 1.3 响应式断点

```
桌面 (≥1200px): 完整双栏/三栏布局
平板 (768px-1199px): 双栏或单栏自适应
手机 (<768px): 单栏堆叠，隐藏侧边栏
```

## 3. 详细设计

### 3.1 全局样式层 (common.css)

新建 `frontend/src/assets/themes/common.css`：

```css
/* 全局 CSS 变量应用 */
body {
  background: var(--bg);
  color: var(--text);
  transition: background-color 0.3s ease, color 0.3s ease;
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
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

/* 通用按钮 */
.btn-primary {
  background: var(--purple);
  color: var(--white);
  border: none;
  padding: 0.5rem 1rem;
  border-radius: var(--r);
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.btn-primary:hover {
  filter: brightness(1.1);
}

/* 状态标签 */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.6rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 600;
}

.badge-success { background: var(--green-bg); color: var(--green); }
.badge-warning { background: var(--amber-bg); color: var(--amber); }
.badge-error   { background: var(--red-bg); color: var(--red); }
.badge-info    { background: var(--blue-bg); color: var(--blue); }
```

### 3.2 ThemeToggle 组件

**文件**: `frontend/src/components/ThemeToggle.vue`

**功能**:
- 显示太阳图标（亮主题）/ 月亮图标（暗主题）
- 点击切换主题
- 动画过渡效果

**样式**:
```css
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
}

.theme-toggle:hover {
  background: var(--bg3);
  border-color: var(--purple);
  color: var(--purple);
}
```

### 3.3 AppLayout Header 重构

**文件**: `frontend/src/components/AppLayout.vue`

**变更**:
1. Header 右侧添加 ThemeToggle 组件
2. 统一 Header 样式使用 CSS 变量
3. 添加响应式：移动端隐藏部分导航

```html
<header class="app-header">
  <div class="header-left">
    <!-- Logo / 标题 -->
  </div>
  <div class="header-right">
    <ThemeToggle />
    <!-- 用户信息 -->
  </div>
</header>
```

### 3.4 页面样式重构清单

| 页面 | 需修改的硬编码颜色 | 响应式调整 |
|------|-------------------|------------|
| HomeView.vue | `#6366f1`, `#4f46e5`, `#1e1b4b`, `#333`, `#666`, `#ddd` | grid auto-fill, 减小 padding |
| ProjectView.vue | `#6366f1`, `#1e1b4b`, `#ddd`, `#e5e7eb` | documents-grid 响应式 |
| ResultsView.vue | `#667eea`, `#333`, `#ddd` | section 响应式 |
| KnowledgeView.vue | `#6366f1`, `#333` | sidebar 响应式折叠 |
| LoginView.vue | `#6366f1`, `#4f46e5` | 居中表单响应式宽度 |
| RegisterView.vue | 同上 | 同上 |

### 3.5 响应式 CSS 示例

```css
/* 通用响应式容器 */
.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 2rem;
}

/* 响应式网格 */
.grid-responsive {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1.5rem;
}

/* 响应式断点 */
@media (max-width: 1199px) {
  .container { padding: 0 1.5rem; }
  .sidebar { display: none; } /* 移动端隐藏侧边栏 */
}

@media (max-width: 767px) {
  .container { padding: 0 1rem; }
  .grid-responsive { grid-template-columns: 1fr; }
  .header { padding: 0.75rem 1rem; }
  .section { padding: 1rem; }
}
```

## 4. useTheme Composable 增强

**文件**: `frontend/src/composables/useTheme.ts`

**现有功能** (保持不变):
- `initTheme()`: 初始化主题
- `toggleTheme()`: 切换主题
- `setTheme(t)`: 设置特定主题
- `theme`: 当前主题 ref

**需增强**:
- 添加系统偏好监听 (`prefers-color-scheme`)
- 添加主题切换动画类

```typescript
// 系统偏好监听
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)')

function handleSystemChange(e: MediaQueryListEvent) {
  if (!localStorage.getItem(STORAGE_KEY)) {
    theme.value = e.matches ? 'dark' : 'light'
    applyTheme(theme.value)
  }
}

prefersDark.addEventListener('change', handleSystemChange)
```

## 5. 实现顺序

### Phase 1: 基础设施
1. 创建 `common.css` 全局样式层
2. 创建 `ThemeToggle.vue` 组件
3. 修改 `AppLayout.vue` Header

### Phase 2: 页面重构
4. 重构 HomeView.vue
5. 重构 ProjectView.vue
6. 重构 ResultsView.vue
7. 重构 KnowledgeView.vue

### Phase 3: 收尾
8. 添加响应式样式到各页面
9. 测试亮/暗主题切换
10. 测试响应式布局

## 6. 文件变更

**新增文件**:
- `frontend/src/assets/themes/common.css`
- `frontend/src/components/ThemeToggle.vue`

**修改文件**:
- `frontend/src/components/AppLayout.vue`
- `frontend/src/views/HomeView.vue`
- `frontend/src/views/ProjectView.vue`
- `frontend/src/views/ResultsView.vue`
- `frontend/src/views/KnowledgeView.vue`
- `frontend/src/App.vue` (导入 common.css)
