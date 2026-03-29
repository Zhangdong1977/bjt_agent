# 知识库优化设计文档

**日期**: 2026-03-29
**状态**: 已确认
**版本**: v1.0

---

## 1. 概述

对知识库模块进行4项优化：
1. 文档列表显示原始文件名
2. 文档详情页显示原始文件名
3. RAG分片Tab默认显示本文档所有分片，支持搜索过滤
4. 首页改造成Google风格搜索+文档列表的70/30双栏布局

---

## 2. 功能详细设计

### 2.1 文档列表显示原名

**问题**：
- `KnowledgeDoc.id` 是 UUID 文件名（如 `24733c84-b2c7-4dae-b492-a5adc6fd4877.docx`）
- `KnowledgeDoc.filename` 才是用户上传时的原始文件名（如 `RTCMS技术规范书.docx`）
- 当前列表用 `id` 显示，导致用户看到无意义的UUID

**修复位置**：`frontend/src/views/KnowledgeView.vue`

**修改内容**：
- 列表渲染时使用 `item.filename` 而非 `item.id`

```vue
<!-- 修改前 -->
<div>{{ item.id }}</div>

<!-- 修改后 -->
<div>{{ item.filename }}</div>
```

**影响范围**：仅UI显示层，无需修改API

---

### 2.2 文档详情页显示原名

**问题**：
- 详情页标题区域未显示文档名
- 需要从API响应中获取 `filename` 并展示

**修复位置**：`frontend/src/components/KnowledgeDocDetail.vue`

**修改内容**：
- 从 `DocumentContentResponse.filename` 获取原名
- 在Drawer标题栏显示文档名

```typescript
// 从API响应获取
const response = await knowledgeApi.getDocumentContent(props.docId)
content.value = response.data
const originalFilename = response.data.filename // 如 "24733c84-b2c7-4dae-b492-a5adc6fd4877.docx"
```

**后端已修复**：`backend/api/knowledge.py` 的 `get_document_content` 已返回 `{content, filename}`

---

### 2.3 RAG分片默认显示全部分片

**需求**：用户打开" RAG分片" Tab时，默认显示该文档的所有分片，搜索框用于过滤

**新增API**：

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/knowledge/documents/{docId}/shards` | GET | 获取指定文档的所有RAG分片 |

**请求参数**：
- `docId`: 文档ID（UUID文件名）

**响应格式**：
```json
{
  "docId": "24733c84-b2c7-4dae-b492-a5adc6fd4877.docx",
  "filename": "RTCMS技术规范书.docx",
  "shards": [
    {
      "id": "shard-1",
      "startLine": 1,
      "endLine": 50,
      "content": "# RTCMS技术规范书\n\n..."
    },
    {
      "id": "shard-2",
      "startLine": 51,
      "endLine": 100,
      "content": "1.1. 技术背景\n\n..."
    }
  ],
  "totalShards": 24
}
```

**实现方案**：

1. **rag_memory_service 新增接口** `GET /api/shards/by-path`
   - 接收 `path` 参数（文档路径）
   - 返回该文档对应的所有分片

2. **backend/api/knowledge.py 新增端点** `GET /documents/{docId}/shards`
   - 调用 rag_memory_service 的新接口
   - 转换路径格式并返回

**前端修改**：`KnowledgeDocDetail.vue`
- Tab切换到"RAG分片"时，自动调用 `getDocumentShards(docId)`
- 搜索框输入时，在前端过滤 `shards` 数组

---

### 2.4 首页70/30双栏布局

**布局设计**：
- **左侧70%**：Google风格搜索区域
  - 大标题"知识库搜索"
  - 搜索输入框 + 搜索按钮
  - 搜索结果列表（来源文档名 + 分片内容 + 相关度）

- **右侧30%**：文档管理区域
  - 上传文档按钮
  - 文档搜索框
  - 文档列表（显示原名）

**文件修改**：`frontend/src/views/KnowledgeView.vue`

**布局结构**：
```vue
<div class="knowledge-container">
  <!-- 左侧：搜索区域 (70%) -->
  <div class="search-panel">
    <h1>知识库搜索</h1>
    <a-input-search @search="onGlobalSearch" />
    <div class="search-results">
      <!-- RAG搜索结果列表 -->
    </div>
  </div>

  <!-- 右侧：文档列表 (30%) -->
  <div class="doc-panel">
    <a-button @click="showUpload">+ 上传文档</a-button>
    <a-input v-model="docSearch" placeholder="搜索文档..." />
    <div class="doc-list">
      <!-- 文档列表 -->
    </div>
  </div>
</div>
```

**新增API**：

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/knowledge/search` | POST | 全局搜索，返回所有文档的分片结果 |

**搜索结果格式**：
```json
{
  "results": [
    {
      "source": "RTCMS技术规范书.docx",
      "snippet": "...相关的内容片段...",
      "score": 0.95,
      "docId": "24733c84-b2c7-4dae-b492-a5adc6fd4877.docx",
      "startLine": 15,
      "endLine": 30
    }
  ],
  "queryTime": 125,
  "totalResults": 45
}
```

---

## 3. 数据流图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (Vue3)                          │
├─────────────────────────────────────────────────────────────────┤
│  KnowledgeView.vue                                              │
│  ┌─────────────────────────────┬───────────────────────────────┤
│  │  左侧: 全局搜索 (70%)       │  右侧: 文档列表 (30%)         │
│  │  ├─ 搜索框                 │  ├─ 上传按钮                   │
│  │  └─ 搜索结果列表           │  ├─ 文档搜索框                 │
│  │     └─ 来源+分片+相关度    │  └─ 文档列表(显示原名)         │
│  └─────────────────────────────┴───────────────────────────────┤
│                                                                 │
│  KnowledgeDocDetail.vue                                         │
│  ├─ Tab: 文档内容 ← getDocumentContent(docId)                   │
│  └─ Tab: RAG分片 ← getDocumentShards(docId)                    │
│                                ↓                                 │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Backend API (FastAPI)                        │
├─────────────────────────────────────────────────────────────────┤
│  GET /knowledge/documents/{docId}/content  → 读 .md 文件       │
│  GET /knowledge/documents/{docId}/shards   → 查 RAG 分片       │
│  POST /knowledge/search                  → 代理全局搜索          │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                  rag_memory_service (Node.js)                  │
├─────────────────────────────────────────────────────────────────┤
│  POST /api/search                     → 全文搜索                │
│  GET  /api/shards/by-path?path=...   → 按路径查分片(新增)     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. 文件修改清单

### Frontend (Vue3)

| 文件 | 修改内容 |
|------|----------|
| `frontend/src/views/KnowledgeView.vue` | 重构为双栏布局，新增全局搜索功能 |
| `frontend/src/components/KnowledgeDocDetail.vue` | 显示原名，RAG分片默认加载全部 |
| `frontend/src/api/client.ts` | 新增 `getDocumentShards`, `globalSearch` 方法 |

### Backend (Python)

| 文件 | 修改内容 |
|------|----------|
| `backend/api/knowledge.py` | 新增 `GET /documents/{docId}/shards` 端点 |
| `backend/api/knowledge.py` | 新增 `POST /search` 端点（代理到RAG服务） |

### RAG Memory Service (Node.js)

| 文件 | 修改内容 |
|------|----------|
| `rag_memory_service/src/routes/shards.ts` | 新增 `GET /api/shards/by-path` 端点 |

---

## 5. 测试计划

1. **文档列表**：上传文档后，列表应显示原名而非UUID
2. **文档详情**：点击详情，Drawer标题应显示原名
3. **RAG分片Tab**：切换到该Tab应自动加载所有分片，搜索可过滤
4. **全局搜索**：左侧搜索应返回所有文档的分片结果，标注来源文档

---

## 6. 优先级

1. **P0**：文档列表和详情显示原名（已在本次会话中修复）
2. **P1**：RAG分片默认显示全部 + 新API
3. **P2**：首页70/30布局重构
