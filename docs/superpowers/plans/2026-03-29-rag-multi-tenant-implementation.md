# RAG 多租户隔离实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 RAG 索引多租户隔离，每个用户的文档索引互相独立

**Architecture:**
- RAG service 维护 `Map<user_id, MemoryIndex>` 缓存，按需加载用户索引
- 每个用户索引路径: `workspace/knowledge/{user_id}/data/memory.sqlite`
- FastAPI 通过 `X-User-ID` header 传递用户身份

**Tech Stack:** Node.js (RAG Service), Python FastAPI

---

## 文件修改清单

### RAG Service
- `rag_memory_service/src/server.ts` - MemoryIndex 缓存管理
- `rag_memory_service/src/routes/search.ts` - X-User-ID header 支持
- `rag_memory_service/src/routes/status.ts` - X-User-ID header 支持
- `rag_memory_service/src/routes/sync.ts` - X-User-ID header 支持

### FastAPI
- `backend/api/knowledge.py` - X-User-ID header 传递

---

## Task 1: RAG Service - 创建 indexManager 模块

**Files:**
- Create: `rag_memory_service/src/indexManager.ts`

- [ ] **Step 1: 创建 indexManager.ts 模块**

```typescript
/**
 * Index Manager
 * 多租户 MemoryIndex 管理器
 */

import { createMemoryIndex, MemoryIndex } from 'rag-memory';
import path from 'node:path';

export interface IndexManagerOptions {
  documentsBasePath: string;  // e.g., /workspace/knowledge
}

export class IndexManager {
  private indices: Map<string, MemoryIndex> = new Map();
  private documentsBasePath: string;

  constructor(options: IndexManagerOptions) {
    this.documentsBasePath = options.documentsBasePath;
  }

  /**
   * Get or create MemoryIndex for a user
   */
  async getIndex(userId: string): Promise<MemoryIndex> {
    // Return cached index if exists
    if (this.indices.has(userId)) {
      return this.indices.get(userId)!;
    }

    // Create new index for user
    const userPath = path.join(this.documentsBasePath, userId);
    const indexPath = path.join(userPath, 'data', 'memory.sqlite');

    const memory = await createMemoryIndex({
      documentsPath: userPath,
      indexPath: indexPath,
      config: {
        embeddings: {
          provider: 'zhipu',
          model: 'embedding-3',
        },
        search: {
          maxResults: 50,
          minScore: 0.0,
          hybrid: {
            enabled: true,
            vectorWeight: 0.7,
            textWeight: 0.3,
            candidateMultiplier: 2.0,
          },
        },
        extraPaths: [userPath],
      },
      initialSync: false,  // Don't sync on creation
    });

    this.indices.set(userId, memory);
    return memory;
  }

  /**
   * Get index status for a user
   */
  async getStatus(userId: string): Promise<{
    status: 'ready' | 'indexing' | 'error';
    files: number;
    chunks: number;
    provider: string;
    model: string;
  } | null> {
    const memory = this.indices.get(userId);
    if (!memory) {
      return null;
    }

    const status = memory.status();
    return {
      status: status.dirty ? 'indexing' : 'ready',
      files: status.files,
      chunks: status.chunks,
      provider: status.provider,
      model: status.model,
    };
  }

  /**
   * Close and remove index for a user
   */
  async closeIndex(userId: string): Promise<void> {
    const memory = this.indices.get(userId);
    if (memory) {
      await memory.close();
      this.indices.delete(userId);
    }
  }

  /**
   * Close all indices
   */
  async closeAll(): Promise<void> {
    for (const [userId, memory] of this.indices) {
      await memory.close();
    }
    this.indices.clear();
  }
}
```

- [ ] **Step 2: 提交**

```bash
git add rag_memory_service/src/indexManager.ts
git commit -m "feat: add IndexManager for multi-tenant MemoryIndex handling"
```

---

## Task 2: RAG Service - 修改 server.ts 集成 IndexManager

**Files:**
- Modify: `rag_memory_service/src/server.ts`

- [ ] **Step 1: 修改 server.ts 集成 IndexManager**

找到 server.ts 中 `initializeMemory` 函数，替换为：

```typescript
// Replace the single memory instance with IndexManager
import { IndexManager } from './indexManager.js';

let indexManager: IndexManager;

async function initializeMemory(config: ServiceConfig): Promise<void> {
  log.info('Initializing index manager...');

  indexManager = new IndexManager({
    documentsBasePath: config.documentsPath,
  });

  log.info('Index manager initialized');
}
```

找到 `gracefulShutdown` 函数，修改为：

```typescript
async function gracefulShutdown(signal: string): Promise<void> {
  log.info(`${signal} received, starting graceful shutdown...`);

  try {
    log.info('Closing all memory indices...');
    await indexManager.closeAll();
    log.info('All memory indices closed');

    log.info('Shutdown complete');
    process.exit(0);
  } catch (error) {
    log.error('Error during shutdown:', error);
    process.exit(1);
  }
}
```

修改 `main()` 函数中：

```typescript
// 初始化后不再需要创建单一 memory 实例
await initializeMemory(config);

// 注册路由时传递 indexManager
registerRoutes(app, indexManager);
```

修改 `registerRoutes` 函数签名和实现：

```typescript
function registerRoutes(app: express.Express, manager: IndexManager): void {
  // Make manager available to all routes
  app.use((req, res, next) => {
    req.indexManager = manager;
    next();
  });
  // ... rest of routes
}
```

在 Express Request 类型扩展中添加：

```typescript
interface Request {
  indexManager?: IndexManager;
}
```

- [ ] **Step 2: 提交**

```bash
git add rag_memory_service/src/server.ts
git commit -m "refactor: integrate IndexManager for per-user memory indices"
```

---

## Task 3: RAG Service - 修改 search.ts 支持 X-User-ID

**Files:**
- Modify: `rag_memory_service/src/routes/search.ts`

- [ ] **Step 1: 修改 search.ts**

修改文件顶部的 Express Request 扩展，添加 indexManager:

```typescript
declare global {
  namespace Express {
    interface Request {
      indexManager?: IndexManager;
    }
  }
}
```

修改 `POST /api/search` handler:

```typescript
router.post(
  '/search',
  asyncHandler(async (req: Request, res: Response) => {
    const userId = req.headers['x-user-id'] as string;

    if (!userId) {
      throw createError('X-User-ID header is required', 400, 'missing_user_id');
    }

    const { query, limit = 10, options = {} }: SearchRequestBody = req.body;

    // ... existing validation code ...

    // Get user's index
    const manager = req.indexManager;
    if (!manager) {
      throw createError('Index manager not initialized', 503, 'service_unavailable');
    }

    const memory = await manager.getIndex(userId);

    // Perform search
    const startTime = Date.now();
    const results = await memory.search(trimmedQuery, {
      maxResults,
      minScore,
    });
    const queryTime = Date.now() - startTime;

    // ... rest of code unchanged ...
  })
);
```

- [ ] **Step 2: 提交**

```bash
git add rag_memory_service/src/routes/search.ts
git commit -m "feat: add X-User-ID support to search endpoint"
```

---

## Task 4: RAG Service - 修改 status.ts 支持 X-User-ID

**Files:**
- Modify: `rag_memory_service/src/routes/status.ts`

- [ ] **Step 1: 修改 status.ts**

添加 indexManager 到 Request 类型：

```typescript
declare global {
  namespace Express {
    interface Request {
      indexManager?: IndexManager;
    }
  }
}
```

修改 `GET /api/status` handler:

```typescript
router.get(
  '/status',
  asyncHandler(async (req: Request, res: Response) => {
    const userId = req.headers['x-user-id'] as string;

    if (!userId) {
      throw createError('X-User-ID header is required', 400, 'missing_user_id');
    }

    const manager = req.indexManager;
    if (!manager) {
      throw createError('Index manager not initialized', 503, 'service_unavailable');
    }

    const status = await manager.getStatus(userId);

    if (!status) {
      // User has no index yet - return empty status
      res.json({
        status: 'ready',
        files: 0,
        chunks: 0,
        provider: 'zhipu',
        model: 'embedding-3',
        lastSync: new Date().toISOString(),
      });
      return;
    }

    res.json({
      ...status,
      lastSync: new Date().toISOString(),
    });
  })
);
```

- [ ] **Step 2: 提交**

```bash
git add rag_memory_service/src/routes/status.ts
git commit -m "feat: add X-User-ID support to status endpoint"
```

---

## Task 5: RAG Service - 修改 sync.ts 支持 X-User-ID

**Files:**
- Modify: `rag_memory_service/src/routes/sync.ts`

- [ ] **Step 1: 修改 sync.ts**

添加 indexManager 到 Request 类型：

```typescript
declare global {
  namespace Express {
    interface Request {
      indexManager?: IndexManager;
    }
  }
}
```

修改 `POST /api/sync` handler:

```typescript
router.post(
  '/sync',
  asyncHandler(async (req: Request, res: Response) => {
    const userId = req.headers['x-user-id'] as string;

    if (!userId) {
      throw createError('X-User-ID header is required', 400, 'missing_user_id');
    }

    const { force = false }: SyncRequestBody = req.body;

    const manager = req.indexManager;
    if (!manager) {
      throw createError('Index manager not initialized', 503, 'service_unavailable');
    }

    // Get user's index (creates if not exists)
    const memory = await manager.getIndex(userId);

    // ... rest of sync logic unchanged (it already uses config.documentsPath) ...
    // But need to modify convertDocumentsToMarkdown to use user's directory
  })
);
```

还需要修改 `convertDocumentsToMarkdown` 函数的调用，因为它需要知道用户目录。修改 sync handler 中的调用：

```typescript
// 在 sync handler 中获取用户目录
const config = getConfig();
const userDocumentsPath = path.join(config.documentsPath, userId);
console.log(`[sync] Converting documents in ${userDocumentsPath}...`);
const convertResult = await convertDocumentsToMarkdown(userDocumentsPath);
```

- [ ] **Step 2: 提交**

```bash
git add rag_memory_service/src/routes/sync.ts
git commit -m "feat: add X-User-ID support to sync endpoint"
```

---

## Task 6: FastAPI - 修改 knowledge.py 传递 X-User-ID

**Files:**
- Modify: `backend/api/knowledge.py`

- [ ] **Step 1: 修改知识库 API**

找到 `sync_knowledge_base` 函数，添加 user_id 参数：

```python
async def sync_knowledge_base(user_id: str = None) -> bool:
    """Trigger rag_memory_service to sync knowledge base index."""
    settings = get_settings()

    headers = {}
    if user_id:
        headers["X-User-ID"] = user_id

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.rag_memory_service_url}/api/sync",
                json={"force": False},
                headers=headers
            )
            return response.status_code == 200
    except httpx.RequestError:
        return False
    except httpx.HTTPStatusError:
        return False
```

找到 `global_search` 函数，添加 user_id 参数：

```python
@router.post("/search")
async def global_search(
    query: str = Body(..., embed=True),
    limit: int = 20,
    current_user: User = Depends(get_current_user)
):
    """全局搜索知识库"""
    rag_settings = get_settings()

    headers = {"X-User-ID": current_user.id}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{rag_settings.rag_memory_service_url}/api/search",
                json={"query": query, "limit": limit},
                headers=headers
            )
            results = response.json()
            # ... rest unchanged ...
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="RAG service unavailable")
```

找到 `get_index_status` 函数，添加 user_id：

```python
@router.get("/index-status")
async def get_index_status(current_user: User = Depends(get_current_user)):
    """获取RAG索引状态"""
    rag_settings = get_settings()
    headers = {"X-User-ID": current_user.id}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{rag_settings.rag_memory_service_url}/api/status",
                headers=headers
            )
            return response.json()
    except httpx.RequestError:
        return {
            "status": "unavailable",
            "files": 0,
            "chunks": 0,
            "provider": "unknown",
            "model": "unknown",
            "lastSync": None
        }
```

还需要修改调用 `sync_knowledge_base()` 的地方，传递 user_id：

- `upload_document` 函数中：`asyncio.create_task(sync_knowledge_base(current_user.id))`
- `delete_document` 函数中：`asyncio.create_task(sync_knowledge_base(current_user.id))`

- [ ] **Step 2: 提交**

```bash
git add backend/api/knowledge.py
git commit -m "feat: pass X-User-ID header to RAG service for multi-tenant isolation"
```

---

## Task 7: 验证多租户隔离

- [ ] **Step 1: 重启 RAG service**

```bash
cd rag_memory_service
npm run build  # 如果有 build 脚本
# 或者直接运行
node dist/server.js  # 或 npm start
```

- [ ] **Step 2: 测试索引状态 API**

```bash
# 使用用户 A 的 token
curl -X GET http://localhost:3001/api/status \
  -H "X-User-ID: 2b4bafee-345f-4d53-890a-210794d49adc"

# 应该返回该用户的索引状态（files, chunks）
```

- [ ] **Step 3: 测试搜索 API**

```bash
curl -X POST http://localhost:3001/api/search \
  -H "Content-Type: application/json" \
  -H "X-User-ID: 2b4bafee-345f-4d53-890a-210794d49adc" \
  -d '{"query": "用电", "limit": 5}'

# 应该只返回该用户文档的搜索结果
```

- [ ] **Step 4: 测试不同用户的结果不同**

使用不同的 X-User-ID 应该返回不同的结果集

---

## 自检清单

- [ ] RAG service 启动无错误
- [ ] `GET /api/status` 需要 X-User-ID header
- [ ] `POST /api/search` 需要 X-User-ID header
- [ ] `POST /api/sync` 需要 X-User-ID header
- [ ] 不同用户的搜索结果互不干扰
- [ ] FastAPI 调用 RAG service 时传递 X-User-ID
- [ ] 索引状态显示正确的用户文档数
- [ ] 无 console.error 或未处理异常
