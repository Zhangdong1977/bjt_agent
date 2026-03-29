# 知识库优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对知识库模块进行4项优化：文档列表/详情显示原名、RAG分片默认显示、首页70/30布局

**Architecture:**
- 后端新增两个API端点：`GET /documents/{docId}/shards` 和 `POST /search`
- RAG分片通过复用现有 `readFile` 接口读取完整.md文件后分片
- 前端改造 KnowledgeView.vue 为双栏布局，改造 KnowledgeDocDetail.vue 支持RAG分片浏览

**Tech Stack:** Vue3 + FastAPI + rag_memory_service (Node.js)

---

## 文件修改清单

### Frontend
- `frontend/src/views/KnowledgeView.vue` - 重构为70/30双栏布局
- `frontend/src/components/KnowledgeDocDetail.vue` - 显示原名 + RAG分片默认加载
- `frontend/src/api/client.ts` - 新增 `getDocumentShards` 和 `globalSearch` 方法

### Backend
- `backend/api/knowledge.py` - 新增两个端点

### RAG Memory Service
- 无需修改（复用现有 `/api/readfile` 接口）

---

## Task 1: 验证 P0 修复（文档列表和详情显示原名）

**Files:**
- Verify: `frontend/src/views/KnowledgeView.vue:142`
- Verify: `frontend/src/components/KnowledgeDocDetail.vue:76`
- Verify: `frontend/src/views/KnowledgeView.vue:81-84`

- [ ] **Step 1: 检查当前代码状态**

确认以下代码已存在：

KnowledgeView.vue 第142行：
```vue
<template #title>
  {{ item.filename }}
</template>
```

KnowledgeView.vue 第81-84行：
```typescript
function openDocDetail(doc: KnowledgeDoc) {
  selectedDoc.value = { id: doc.id, name: doc.filename }
  showDocDetail.value = true
}
```

KnowledgeDocDetail.vue 第76行：
```vue
:title="docName"
```

- [ ] **Step 2: 提交（如已修复）**

如果代码已正确，显示"已修复，无需操作"

---

## Task 2: 新增后端 API - 获取文档所有分片

**Files:**
- Modify: `backend/api/knowledge.py` - 在现有路由文件中添加新端点

- [ ] **Step 1: 添加新端点 `GET /documents/{doc_id}/shards`**

在 `backend/api/knowledge.py` 文件末尾添加：

```python
@router.get("/documents/{document_id}/shards")
async def get_document_shards(
    document_id: str,
    current_user: User = Depends(get_current_user)
):
    """获取文档的所有RAG分片"""
    user_dir = os.path.join(settings.knowledge_base_path, current_user.id)
    # 解析后的markdown文件路径
    md_file_path = os.path.join(user_dir, f"{document_id}.md")

    if not os.path.exists(md_file_path):
        raise HTTPException(status_code=404, detail="Document not found")

    # 安全检查
    if not md_file_path.startswith(os.path.abspath(user_dir)):
        raise HTTPException(status_code=403, detail="Access denied")

    # 读取文件内容
    with open(md_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 按50行分片
    lines = content.split('\n')
    total_lines = len(lines)
    chunk_size = 50
    shards = []

    for i in range(0, total_lines, chunk_size):
        chunk_lines = lines[i:i + chunk_size]
        shard_content = '\n'.join(chunk_lines)
        shards.append({
            "id": f"shard-{i // chunk_size + 1}",
            "startLine": i + 1,
            "endLine": min(i + chunk_size, total_lines),
            "content": shard_content
        })

    return {
        "docId": document_id,
        "filename": document_id,  # 保持与现有接口一致
        "shards": shards,
        "totalShards": len(shards)
    }
```

- [ ] **Step 2: 运行测试验证**

启动后端服务后测试：
```bash
curl -X GET "http://localhost:8000/api/knowledge/documents/24733c84-b2c7-4dae-b492-a5adc6fd4877.docx/shards" \
  -H "Authorization: Bearer <token>"
```

预期返回 JSON 包含 `shards` 数组

- [ ] **Step 3: 提交**

```bash
git add backend/api/knowledge.py
git commit -m "feat: add GET /documents/{id}/shards endpoint for RAG shards"
```

---

## Task 3: 新增后端 API - 全局搜索

**Files:**
- Modify: `backend/api/knowledge.py` - 添加搜索端点

- [ ] **Step 1: 添加 `POST /search` 端点**

在 `backend/api/knowledge.py` 文件中现有端点后添加：

```python
@router.post("/search")
async def global_search(
    query: str = Body(...),
    limit: int = 20,
    current_user: User = Depends(get_current_user)
):
    """全局搜索知识库"""
    rag_settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{rag_settings.rag_memory_service_url}/api/search",
                json={"query": query, "limit": limit}
            )
            results = response.json()

            # 格式化结果，添加文档ID
            formatted_results = []
            for r in results.get("results", []):
                # 从path中提取文档ID（path格式: /path/to/doc.docx.md）
                path = r.get("path", "")
                doc_id = os.path.basename(path).replace(".md", "")
                formatted_results.append({
                    "source": r.get("path", ""),
                    "snippet": r.get("snippet", ""),
                    "score": r.get("score", 0),
                    "docId": doc_id,
                    "startLine": r.get("startLine", 0),
                    "endLine": r.get("endLine", 0)
                })

            return {
                "results": formatted_results,
                "queryTime": results.get("queryTime", 0),
                "totalResults": results.get("totalResults", 0)
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
```

- [ ] **Step 2: 测试端点**

```bash
curl -X POST "http://localhost:8000/api/knowledge/search" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "RTCMS", "limit": 10}'
```

预期返回包含 `results` 数组，每项有 `source`, `snippet`, `score`, `docId`

- [ ] **Step 3: 提交**

```bash
git add backend/api/knowledge.py
git commit -m "feat: add POST /search endpoint for global knowledge base search"
```

---

## Task 4: 前端 - 新增 API 方法

**Files:**
- Modify: `frontend/src/api/client.ts` - 添加新方法

- [ ] **Step 1: 添加 `getDocumentShards` 和 `globalSearch` 方法**

在 `knowledgeApi` 对象中添加：

```typescript
getDocumentShards: (docId: string) => {
  return apiClient.get(`/knowledge/documents/${docId}/shards`)
},

globalSearch: (query: string, limit: number = 20) => {
  return apiClient.post('/knowledge/search', { query, limit })
},
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/api/client.ts
git commit -m "feat: add getDocumentShards and globalSearch API methods"
```

---

## Task 5: 前端 - 改造文档详情组件（RAG分片默认显示全部）

**Files:**
- Modify: `frontend/src/components/KnowledgeDocDetail.vue`

- [ ] **Step 1: 添加新状态和方法**

在 `<script setup>` 中修改：

```typescript
// 新增状态
const activeTab = ref('content')
const allShards = ref<any[]>([])
const filteredShards = ref<any[]>([])
const shardFilter = ref('')

// 新增方法：加载所有分片
async function loadAllShards() {
  if (!props.docId) return
  searching.value = true
  try {
    const response = await knowledgeApi.getDocumentShards(props.docId)
    allShards.value = response.data.shards || []
    filteredShards.value = allShards.value
  } catch (err) {
    console.error('Failed to load shards:', err)
  } finally {
    searching.value = false
  }
}

// 监听Tab切换
watch(activeTab, (tab) => {
  if (tab === 'rag') {
    loadAllShards()
  }
})

// 过滤分片
watch(shardFilter, (filter) => {
  if (!filter.trim()) {
    filteredShards.value = allShards.value
  } else {
    filteredShards.value = allShards.value.filter((s: any) =>
      s.content.toLowerCase().includes(filter.toLowerCase())
    )
  }
})
```

- [ ] **Step 2: 修改 RAG分片 Tab 模板**

将现有 RAG 分片 Tab（第88-112行）替换为：

```vue
<a-tab-pane key="rag" tab="RAG分片">
  <div class="rag-search">
    <a-input
      v-model:value="shardFilter"
      placeholder="在分片中搜索..."
      allow-clear
      style="margin-bottom: 16px;"
    />
    <a-spin :spinning="searching">
      <div v-if="filteredShards.length > 0" class="shards-list">
        <p class="shard-info">
          共 {{ filteredShards.length }} 个分片
        </p>
        <div v-for="shard in filteredShards" :key="shard.id" class="shard-item">
          <div class="shard-header">
            <Tag>分片 {{ shard.id.replace('shard-', '') }}</Tag>
            <span class="shard-lines">第 {{ shard.startLine }}-{{ shard.endLine }} 行</span>
          </div>
          <pre class="shard-content">{{ shard.content }}</pre>
        </div>
      </div>
      <Empty v-else-if="!searching" description="暂无分片数据" />
    </a-spin>
  </div>
</a-tab-pane>
```

- [ ] **Step 3: 添加样式**

在 `<style>` 中添加：

```css
.shards-list {
  max-height: 500px;
  overflow-y: auto;
}

.shard-info {
  color: #666;
  margin-bottom: 12px;
}

.shard-item {
  background: #fafafa;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}

.shard-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.shard-lines {
  font-size: 12px;
  color: #999;
}

.shard-content {
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: white;
  padding: 8px;
  border-radius: 4px;
  max-height: 200px;
  overflow-y: auto;
}
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/KnowledgeDocDetail.vue
git commit -m "feat: add default shard loading and filtering in KnowledgeDocDetail"
```

---

## Task 6: 前端 - 改造首页为 70/30 双栏布局

**Files:**
- Modify: `frontend/src/views/KnowledgeView.vue`

- [ ] **Step 1: 添加新状态**

在 `<script setup>` 中添加：

```typescript
// 全局搜索相关
const globalQuery = ref('')
const globalResults = ref<any[]>([])
const globalSearching = ref(false)
const globalSearched = ref(false)

// 文档过滤
const docFilter = ref('')
const filteredDocs = computed(() => {
  if (!docFilter.value.trim()) return docs.value
  return docs.value.filter(d =>
    d.filename.toLowerCase().includes(docFilter.value.toLowerCase())
  )
})
```

- [ ] **Step 2: 添加全局搜索方法**

```typescript
async function onGlobalSearch() {
  if (!globalQuery.value.trim()) return
  globalSearching.value = true
  globalSearched.value = true
  try {
    const response = await knowledgeApi.globalSearch(globalQuery.value)
    globalResults.value = response.data.results || []
  } catch (err) {
    console.error('Global search failed:', err)
    globalResults.value = []
  } finally {
    globalSearching.value = false
  }
}

function openDocFromSearch(docId: string) {
  const doc = docs.value.find(d => d.id === docId)
  if (doc) {
    openDocDetail(doc)
  }
}
```

- [ ] **Step 3: 重构模板为双栏布局**

将整个 `<template>` 内容替换为：

```vue
<template>
  <div class="knowledge-view">
    <a-breadcrumb class="breadcrumb">
      <a-breadcrumb-item>首页</a-breadcrumb-item>
      <a-breadcrumb-item>知识库</a-breadcrumb-item>
    </a-breadcrumb>

    <div class="knowledge-container">
      <!-- 左侧：搜索区域 (70%) -->
      <div class="search-panel">
        <div class="search-header">
          <h1>知识库搜索</h1>
          <p class="search-subtitle">输入关键词搜索文档内容</p>
        </div>

        <div class="search-box">
          <a-input-search
            v-model:value="globalQuery"
            placeholder="输入关键词搜索..."
            size="large"
            @search="onGlobalSearch"
          >
            <template #enterButton>
              <a-button type="primary" :loading="globalSearching">搜索</a-button>
            </template>
          </a-input-search>
        </div>

        <div class="search-results">
          <a-spin :spinning="globalSearching">
            <div v-if="globalResults.length > 0" class="results-list">
              <p class="results-count">
                找到 {{ globalResults.length }} 个结果
              </p>
              <div
                v-for="(result, idx) in globalResults"
                :key="idx"
                class="result-item"
                @click="openDocFromSearch(result.docId)"
              >
                <div class="result-header">
                  <file-text-outlined style="color: #6366f1" />
                  <span class="result-source">{{ result.source }}</span>
                  <Tag :color="getScoreColor(result.score)">{{ result.score.toFixed(2) }}</Tag>
                </div>
                <p class="result-snippet">{{ result.snippet }}</p>
              </div>
            </div>
            <Empty
              v-else-if="globalSearched && !globalSearching"
              description="未找到相关结果"
            />
            <div v-else class="search-hint">
              <p>输入关键词开始搜索知识库</p>
            </div>
          </a-spin>
        </div>
      </div>

      <!-- 右侧：文档列表 (30%) -->
      <div class="doc-panel">
        <div class="doc-panel-header">
          <h3>文档列表</h3>
          <a-button type="primary" @click="toggleUpload">
            <template #icon><PlusOutlined /></template>
            上传文档
          </a-button>
        </div>

        <a-input
          v-model:value="docFilter"
          placeholder="搜索文档..."
          allow-clear
          style="margin-bottom: 12px;"
        />

        <a-list
          :loading="loading"
          :dataSource="filteredDocs"
          item-layout="horizontal"
          :pagination="{ pageSize: 10 }"
        >
          <template #renderItem="{ item }">
            <a-list-item>
              <template #actions>
                <a @click="previewDoc(item)">预览</a>
                <a @click="openDocDetail(item)">详情</a>
                <a-popconfirm
                  title="确定要删除此文档吗？"
                  @confirm="deleteDoc(item.id)"
                >
                  <a class="delete-link" href="javascript:void(0)">删除</a>
                </a-popconfirm>
              </template>
              <a-list-item-meta>
                <template #avatar>
                  <file-text-outlined style="font-size: 24px; color: #6366f1" />
                </template>
                <template #title>{{ item.filename }}</template>
                <template #description>
                  上传于 {{ new Date(item.created_at).toLocaleString() }}
                </template>
              </a-list-item-meta>
            </a-list-item>
          </template>

          <template #emptyText>
            <a-empty description="暂无上传文档" />
          </template>
        </a-list>
      </div>
    </div>

    <!-- 上传 Drawer -->
    <a-drawer
      v-model:open="showUpload"
      title="上传文档"
      width="500"
    >
      <!-- 现有上传表单内容保持不变 -->
    </a-drawer>

    <!-- 文档详情 Drawer -->
    <KnowledgeDocDetail
      v-model:visible="showDocDetail"
      :doc-id="selectedDoc?.id || ''"
      :doc-name="selectedDoc?.name || ''"
    />
  </div>
</template>
```

- [ ] **Step 4: 添加样式**

在 `<style>` 中添加/修改：

```css
.knowledge-container {
  display: flex;
  gap: 24px;
  height: calc(100vh - 180px);
}

.search-panel {
  flex: 7;
  display: flex;
  flex-direction: column;
  padding: 24px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.search-header {
  text-align: center;
  margin-bottom: 24px;
}

.search-header h1 {
  font-size: 32px;
  margin-bottom: 8px;
  color: #1a1a1a;
}

.search-subtitle {
  color: #666;
  margin-bottom: 0;
}

.search-box {
  max-width: 600px;
  margin: 0 auto 24px;
  width: 100%;
}

.search-box :deep(.ant-input-search) {
  display: flex;
  gap: 8px;
}

.search-results {
  flex: 1;
  overflow-y: auto;
  padding: 0 20px;
}

.results-count {
  color: #666;
  margin-bottom: 16px;
}

.result-item {
  background: #fafafa;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
  cursor: pointer;
  transition: background 0.2s;
}

.result-item:hover {
  background: #f0f0f0;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.result-source {
  flex: 1;
  font-weight: 500;
  color: #1890ff;
}

.result-snippet {
  font-size: 13px;
  line-height: 1.6;
  color: #333;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.search-hint {
  text-align: center;
  color: #999;
  padding: 60px 0;
}

.doc-panel {
  flex: 3;
  display: flex;
  flex-direction: column;
  padding: 24px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  min-width: 320px;
}

.doc-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.doc-panel-header h3 {
  margin: 0;
}

.doc-panel :deep(.ant-list-item) {
  padding: 12px 0;
}

.delete-link {
  color: #ff4d4f;
}
```

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/KnowledgeView.vue
git commit -m "feat: redesign KnowledgeView with 70/30 split layout"
```

---

## Task 7: 集成测试

**Files:**
- Test: `frontend/src/views/KnowledgeView.vue`
- Test: `frontend/src/components/KnowledgeDocDetail.vue`

- [ ] **Step 1: 测试文档列表显示原名**

1. 打开知识库页面
2. 确认文档列表显示原始文件名（如"RTCMS技术规范书.docx"）而非UUID

- [ ] **Step 2: 测试文档详情显示原名**

1. 点击文档"详情"按钮
2. 确认Drawer标题显示原始文件名

- [ ] **Step 3: 测试RAG分片默认显示**

1. 打开文档详情
2. 切换到"RAG分片"标签
3. 确认自动加载并显示所有分片
4. 在搜索框输入关键词，确认可以过滤分片

- [ ] **Step 4: 测试全局搜索**

1. 在左侧搜索框输入关键词
2. 确认返回所有文档的分片结果
3. 点击结果，确认可以打开对应文档详情

---

## 自检清单

- [ ] 所有 API 端点已添加并测试通过
- [ ] 文档列表显示原名功能正常
- [ ] 文档详情标题显示原名功能正常
- [ ] RAG分片Tab默认加载所有分片
- [ ] RAG分片搜索过滤功能正常
- [ ] 首页双栏布局（70/30）显示正常
- [ ] 全局搜索返回正确结果
- [ ] 点击搜索结果可打开对应文档详情
- [ ] 无 console.error 或未处理异常
- [ ] 代码符合项目规范（TypeScript类型、Vue3 Composition API）
