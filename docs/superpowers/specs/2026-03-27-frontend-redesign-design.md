# 前端重构设计规范

## 概述

对标书审查系统前端进行重构，新增三个功能模块，使用 Ant Design Vue 提升界面美感，重点优化时间线控件。

## 技术栈

- **UI组件库**：Ant Design Vue 4.x
- **状态管理**：Pinia（现有）
- **样式**：CSS变量 + Ant Design主题定制

## 路由结构

```
/home              → 默认重定向到 /home/check
/home/check        → 标书检查（新项目创建）
/home/history      → 历史标书（项目列表）
/home/knowledge    → 知识库（企业文档）
/projects/:id      → 项目详情（保持现有逻辑）
/projects/:id/review → 审查时间线
/projects/:id/results → 审查结果
```

## 布局规范

```
┌────────────────────────────────────────────────────────┐
│  Header (64px): Logo + 标题 + 用户信息 + Logout        │
├──────────────┬─────────────────────────────────────────┤
│              │                                         │
│  Sidebar    │        Main Content                     │
│  (200px)    │        (flex: 1)                        │
│              │                                         │
│  · 标书检查   │   [路由内容]                             │
│  · 历史标书   │                                         │
│  · 知识库     │                                         │
│              │                                         │
└──────────────┴─────────────────────────────────────────┘
```

- **Header**: 白色背景 + 底部1px边框(#e8e8e8)，Logo左侧，操作用右侧
- **Sidebar**: 浅灰背景(#fafafa) + 左侧2px紫色选中条

## 配色方案

```css
:root {
  --primary: #6366f1;        /* 主色-紫色 */
  --primary-dark: #4f46e5;   /* 主色-深 */
  --primary-light: #a5b4fc;  /* 主色-浅 */
  --success: #52c41a;        /* 成功-绿 */
  --warning: #faad14;         /* 警告-黄 */
  --error: #ff4d4f;           /* 错误-红 */
  --text-primary: #1e1b4b;    /* 主文本-深紫 */
  --text-secondary: #666666;  /* 次文本 */
  --bg-page: #f5f3ff;         /* 页面背景-浅紫灰 */
  --bg-card: #ffffff;         /* 卡片背景 */
  --border: #e8e8e8;          /* 边框 */
}
```

## 组件规范

### 按钮
- Primary: 紫色背景(#6366f1) + 白色文字 + hover提升阴影
- Default: 白色背景 + 紫色边框 + hover紫色填充
- Danger: 红色背景(#ff4d4f)

### 卡片
- 白色背景 + 圆角12px + 阴影`0 2px 8px rgba(0,0,0,0.08)`
- Hover: 阴影增强`0 8px 24px rgba(99,102,241,0.15)` + 上移2px

### 时间线（重点美化）
```vue
<!-- 节点样式 -->
step-pending:  圆形(32px) + 灰色背景 + 虚线边框
step-running:  圆形(32px) + 黄色背景 + 旋转加载动画
step-completed: 圆形(32px) + 绿色背景 + 勾选图标
step-error:    圆形(32px) + 红色背景 + ×图标

<!-- 连接线 -->
pending: 虚线 + 灰色
running: 实线 + 黄色 + 脉冲动画
completed: 实线 + 绿色 + 加粗

<!-- 内容卡片 -->
tool_call: 左侧橙色(4px)边条 + 橙色图标
observation: 左侧绿色(4px)边条 + 绿色图标
thought: 左侧蓝色(4px)边条 + 蓝色图标
```

## 各模块详细设计

### 标书检查 (`/home/check`)
- 创建项目卡片居中显示
- 表单使用Ant Design Form组件
- 上传区域使用Ant Design Upload，拖拽上传
- 文档类型用Tag区分（招标书-蓝色/应标书-绿色）

### 历史标书 (`/home/history`)
- 使用Ant Design Table展示项目列表
- 支持按名称搜索、状态筛选
- 操作列：查看结果、删除
- 空状态使用Ant Design Empty组件

### 知识库 (`/home/knowledge`)
- 上传区域 + 文件列表
- 使用Ant Design List展示文档
- 支持预览（PDF/图片）
- 支持删除

### 页面标题
每个模块显示当前模块名称作为页面标题，添加面包屑导航（首页 / 模块名）

## 文件结构

```
frontend/src/
├── App.vue                    # 主应用（更新布局）
├── router/index.ts           # 路由配置（更新）
├── views/
│   ├── HomeView.vue          # 首页容器（包含sidebar+内容区）
│   ├── CheckView.vue         # 标书检查（新）
│   ├── HistoryView.vue       # 历史标书（新）
│   ├── KnowledgeView.vue     # 知识库（新）
│   ├── ProjectView.vue       # 项目详情（保留）
│   ├── ReviewTimelineView.vue # 审查时间线（更新美化）
│   └── ResultsView.vue       # 审查结果（保留）
├── components/
│   ├── AppLayout.vue         # 主布局组件（新）
│   ├── AppSidebar.vue         # 侧边栏组件（新）
│   └── ReviewTimeline.vue    # 时间线组件（重构）
└── stores/
    └── project.ts            # 项目状态（更新）
```

## 实施顺序

1. 安装 Ant Design Vue 依赖
2. 创建布局组件（AppLayout, AppSidebar）
3. 配置新路由结构
4. 实现 CheckView（标书检查）
5. 实现 HistoryView（历史标书）
6. 实现 KnowledgeView（知识库）
7. 美化 ReviewTimelineView（时间线）
8. 测试与调整
