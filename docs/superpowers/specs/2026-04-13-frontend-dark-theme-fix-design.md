# 前端暗主题适配优化设计方案

**日期：** 2026-04-13
**类型：** 样式/主题优化
**状态：** 已确认

## 背景

当前前端存在暗主题适配问题，部分组件未正确响应主题变更，影响用户体验。

## 问题清单

### 1. AppSidebar.vue（左侧导航栏）
| 问题 | 位置 | 当前值 | 修复后 |
|------|------|--------|--------|
| 硬编码主题 | Line 34 | `theme="light"` | 动态 `:theme="theme"` |
| 硬编码背景色 | CSS | `#fafafa` | `var(--bg2)` |
| 硬编码边框色 | CSS | `#e8e8e8` | `var(--line)` |

### 2. HomeView.vue（首页 + 创建项目弹窗）
| 问题 | 位置 | 当前值 | 修复后 |
|------|------|--------|--------|
| body 背景硬编码 | App.vue | `#f5f3ff` | `var(--bg)` |
| 模态框背景 | CSS | 无变量 | `var(--bg1)` |
| 表单边框 | CSS | 无变量 | `var(--line)` |

### 3. UploadPanel.vue（上传组件）
| 问题 | 当前值 | 修复后 |
|------|--------|--------|
| 卡片边框 | `#ddd` 硬编码 | `var(--line)` |
| 标题颜色 | `#333` 硬编码 | `var(--text)` |
| 上传虚线边框 | `#6366f1` 硬编码 | `var(--purple)` |
| 上传文字颜色 | `#6366f1` 硬编码 | `var(--purple)` |
| 上传 hover 背景 | `#f5f3ff` 硬编码 | `var(--purple-bg)` |
| 状态徽章颜色 | 全部硬编码 | 使用 CSS 变量 |

### 4. ProjectView.vue（项目页面）
| 问题 | 当前值 | 修复后 |
|------|--------|--------|
| section 背景使用不存在的变量 | `var(--white)` | `var(--bg1)` |
| document-card 背景 | `var(--white)` | `var(--bg1)` |

## 修复方案（逐组件修复）

### 步骤 1：AppSidebar.vue
1. 导入 `useTheme` 获取当前主题
2. 动态绑定 `:theme="theme.value"`
3. CSS 变量替换（背景、边框）
4. 保留紫色强调色的选中态样式

### 步骤 2：HomeView.vue
1. App.vue 中 body 背景改为 `var(--bg)`
2. 模态框使用 `var(--bg1)`
3. 表单输入框使用 `var(--line)`

### 步骤 3：UploadPanel.vue
1. 全部硬编码颜色替换为 CSS 变量
2. 参考 ReviewExecution 风格使用 `var(--line)` 边框
3. 保持虚线边框风格但使用主题色

### 步骤 4：ProjectView.vue
1. 替换 `var(--white)` 为 `var(--bg1)`

## 验收标准

1. 暗主题下 AppSidebar 显示深色背景 + 浅色文字
2. 切换主题后所有组件正确响应
3. 上传区域视觉风格与 ReviewExecution 页面协调
4. 无硬编码颜色残留

## 影响范围

- `frontend/src/components/AppSidebar.vue`
- `frontend/src/views/HomeView.vue`
- `frontend/src/components/UploadPanel.vue`
- `frontend/src/views/ProjectView.vue`
- `frontend/src/App.vue`
