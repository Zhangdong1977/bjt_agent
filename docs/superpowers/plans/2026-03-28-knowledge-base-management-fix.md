# 知识库管理模块修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复知识库管理模块，使文档上传、索引、搜索功能正常工作

**Architecture:** 知识库模块涉及两个独立服务：
1. **FastAPI后端** (端口8000): 处理文档上传/删除/预览的HTTP API
2. **rag_memory_service** (端口3001): 基于rag-memory的文档索引和搜索服务

两个服务需要共享同一文档目录，且文档上传后需要触发索引同步。

**Tech Stack:** FastAPI, Vue3, TypeScript, rag-memory (Node.js/Express), SQLite

---

## 问题诊断

| # | 问题 | 影响 |
|---|------|------|
| 1 | rag_memory_service未运行 | 搜索功能完全不可用 |
| 2 | 文档路径不一致 (后端`./workspace/knowledge` vs rag_service`./knowledge_docs`) | 文档无法被索引 |
| 3 | 前端预览URL路径错误 (`/api/knowledge/${doc.id}/preview` vs `/knowledge/documents/${doc.id}/preview`) | 预览功能404 |
| 4 | 文档上传后未触发rag_service同步 | 新上传文档无法被搜索 |
| 5 | .env配置文件缺失 | rag_memory_service无法正常启动 |

---

## 文件结构

```
backend/
├── api/knowledge.py          # 修改: 上传后触发sync
├── config.py                 # 修改: 添加KNOWLEDGE_BASE_DIR配置
├── schemas/knowledge.py      # 现有
rag_memory_service/
├── src/server.ts             # 修改: 支持外部文档路径配置
├── .env.example              # 参考
frontend/
├── src/views/KnowledgeView.vue   # 修改: 修复预览URL
├── src/api/client.ts             # 现有
scripts/
├── bjt.sh                    # 修改: 添加rag_memory_service管理
docs/
├── superpowers/plans/        # 本计划
```

---

## Task 1: 修复前端预览URL路径

**Files:**
- Modify: `frontend/src/views/KnowledgeView.vue:60`

- [ ] **Step 1: 修改预览URL路径**

将:
```typescript
async function previewDoc(doc: KnowledgeDoc) {
  window.open(`/api/knowledge/${doc.id}/preview`, '_blank')
}
```

改为:
```typescript
async function previewDoc(doc: KnowledgeDoc) {
  window.open(`/api/knowledge/documents/${doc.id}/preview`, '_blank')
}
```

- [ ] **Step 2: 验证修改**

确认URL路径与后端路由 `/knowledge/documents/{document_id}/preview` 匹配。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/views/KnowledgeView.vue
git commit -m "fix: correct knowledge preview URL path"
```

---

## Task 2: 统一文档存储路径

**Files:**
- Modify: `backend/api/knowledge.py:15`
- Modify: `backend/config.py`

**问题:** 后端默认使用 `./workspace/knowledge`，但rag_memory_service使用 `./knowledge_docs`。需要统一。

- [ ] **Step 1: 在backend/config.py添加KNOWLEDGE_BASE_DIR配置**

在Settings类中添加:
```python
# Knowledge Base
knowledge_base_dir: Path = Path("./workspace/knowledge")
```

- [ ] **Step 2: 修改knowledge.py使用配置**

```python
from backend.config import get_settings

settings = get_settings()
KNOWLEDGE_BASE_DIR = settings.knowledge_base_dir  # 替换原来的os.environ.get
```

- [ ] **Step 3: 提交**

```bash
git add backend/config.py backend/api/knowledge.py
git commit -m "feat: add knowledge_base_dir config and use shared path"
```

---

## Task 3: 文档上传后触发RAG同步

**Files:**
- Modify: `backend/api/knowledge.py`

**问题:** 上传文档后没有触发rag_memory_service同步，导致新文档无法被搜索。

- [ ] **Step 1: 添加sync_knowledge_base函数**

在 `backend/api/knowledge.py` 末尾添加:
```python
async def sync_knowledge_base():
    """Trigger rag_memory_service to sync knowledge base index."""
    import httpx
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.rag_memory_service_url}/api/sync",
                json={"force": False}
            )
            return response.status_code == 200
    except Exception:
        return False
```

- [ ] **Step 2: 在upload_document中调用sync**

在上传成功后添加:
```python
# 触发知识库同步（异步，不阻塞响应）
asyncio.create_task(sync_knowledge_base())
```

注意: 需要在文件顶部添加 `import asyncio`

- [ ] **Step 3: 提交**

```bash
git add backend/api/knowledge.py
git commit -m "feat: trigger RAG sync after document upload"
```

---

## Task 4: 配置rag_memory_service环境

**Files:**
- Create: `rag_memory_service/.env`
- Modify: `rag_memory_service/src/server.ts` (如需要)

**问题:** rag_memory_service需要正确的环境配置才能运行。

- [ ] **Step 1: 创建rag_memory_service/.env**

```bash
cd /home/openclaw/bjt_agent/rag_memory_service

# 使用workspace/knowledge作为文档目录（与后端共享）
echo 'DOCUMENTS_PATH=/home/openclaw/bjt_agent/workspace/knowledge' > .env
echo 'INDEX_PATH=/home/openclaw/bjt_agent/rag_memory_service/data/memory.sqlite' >> .env
echo 'PORT=3001' >> .env
echo 'HOST=0.0.0.0' >> .env
echo 'ZHIPU_API_KEY=your_zhipu_api_key_here' >> .env
echo 'EMBEDDING_MODEL=embedding-3' >> .env
echo 'VECTOR_WEIGHT=0.7' >> .env
echo 'BM25_WEIGHT=0.3' >> .env
echo 'MAX_SEARCH_RESULTS=50' >> .env
```

- [ ] **Step 2: 创建必要的目录**

```bash
mkdir -p /home/openclaw/bjt_agent/rag_memory_service/data
```

- [ ] **Step 3: 提交**

```bash
git add rag_memory_service/.env.example
git commit -m "docs: add .env.example with all configuration options"
```

---

## Task 5: 更新服务管理脚本

**Files:**
- Modify: `scripts/bjt.sh`

**问题:** bjt.sh需要能够管理rag_memory_service。

- [ ] **Step 1: 检查bjt.sh结构**

```bash
cat /home/openclaw/bjt_agent/scripts/bjt.sh
```

- [ ] **Step 2: 添加rag_memory_service到start/stop/status命令**

需要添加类似现有的backend/worker启动逻辑:
```bash
# rag_memory_service
rag_memory_pid=$(get_pid "rag-memory")
if [ -n "$rag_memory_pid" ]; then
    echo "rag_memory_service is running (PID: $rag_memory_pid)"
else
    echo "rag_memory_service is not running"
fi
```

并添加启动逻辑（参考backend启动方式）:
```bash
start_rag_memory() {
    cd "$APP_DIR/rag_memory_service"
    nohup node src/server.js > "$LOG_DIR/rag_memory.log" 2>&1 &
    echo $! > "$PID_DIR/rag_memory.pid"
}
```

- [ ] **Step 3: 提交**

```bash
git add scripts/bjt.sh
git commit -m "feat: add rag_memory_service management to bjt.sh"
```

---

## Task 6: 验证完整流程

**Files:**
- N/A (手动测试)

- [ ] **Step 1: 启动所有服务**

```bash
./scripts/bjt.sh stop
./scripts/bjt.sh start
```

- [ ] **Step 2: 检查rag_memory_service状态**

```bash
curl -s http://localhost:3001/api/health || echo "Service not responding"
curl -s http://localhost:3001/api/status || echo "Status endpoint error"
```

- [ ] **Step 3: 测试文档上传**

1. 登录前端
2. 进入知识库页面
3. 上传一个PDF或DOCX文件
4. 检查文件是否出现在列表中

- [ ] **Step 4: 测试RAG搜索**

```bash
curl -X POST http://localhost:3001/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "测试", "limit": 5}'
```

- [ ] **Step 5: 测试预览功能**

在浏览器中点击文档预览按钮，检查是否正确打开

---

## 验证清单

| 功能 | 预期行为 | 验证方法 |
|------|---------|---------|
| 文档上传 | 文件保存到workspace/knowledge/{user_id}/ | 检查文件系统 |
| 上传后同步 | rag_memory_service自动索引新文档 | 搜索能返回新文档 |
| 文档列表 | 显示用户所有上传文档 | 前端页面 |
| 文档预览 | 正确打开预览或下载 | 点击预览按钮 |
| 文档删除 | 从文件系统和索引中移除 | 删除后搜索不再返回 |
| RAG搜索 | 返回相关文档片段和来源 | 调用/api/search |

---

## 风险与限制

1. **API Key配置**: rag_memory_service需要有效的ZHIPU_API_KEY才能运行embedding功能
2. **大文件处理**: PDF/DOCX解析依赖服务器资源，上传超大文件可能超时
3. **同步延迟**: 上传后同步是异步的，可能有1-2秒延迟才能被搜索到
