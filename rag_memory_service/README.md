# rag-memory HTTP Service

独立的 HTTP 服务，封装 [rag-memory](../rag-memory/) npm 包，提供知识库检索的 REST API。

## 功能特性

- 混合检索（向量嵌入 + BM25 关键词搜索）
- RESTful API 接口
- 自动索引同步
- 支持智谱 AI 嵌入模型
- SQLite 本地索引存储

## 快速开始

### 1. 安装依赖

```bash
npm install
```

### 2. 配置环境变量

复制 `.env.example` 到 `.env` 并配置：

```bash
cp .env.example .env
```

必需的环境变量：
- `ZHIPU_API_KEY`: 智谱 AI API 密钥

可选的环境变量：
- `PORT`: 服务端口（默认 3001）
- `DOCUMENTS_PATH`: 文档目录（默认 ./knowledge_docs）
- `INDEX_PATH`: 索引文件路径（默认 ./data/memory.sqlite）

### 3. 准备知识库文档

将 Markdown 文档放入 `knowledge_docs/` 目录。

### 4. 构建并启动

```bash
# 开发模式
npm run dev

# 生产模式
npm run build
npm start
```

服务将在 `http://localhost:3001` 启动。

## API 接口

### POST /api/search

搜索知识库

**请求体：**
```json
{
  "query": "安全帽使用规范",
  "limit": 10
}
```

**响应：**
```json
{
  "results": [
    {
      "path": "ppe.md",
      "startLine": 10,
      "endLine": 20,
      "score": 0.95,
      "snippet": "安全帽必须正确佩戴..."
    }
  ],
  "queryTime": 45,
  "totalResults": 1
}
```

### GET /api/status

获取服务状态

**响应：**
```json
{
  "status": "ready",
  "files": 5,
  "chunks": 123,
  "provider": "zhipu",
  "model": "embedding-2",
  "lastSync": "2025-02-01T10:30:00Z"
}
```

### POST /api/sync

同步索引

**请求体：**
```json
{
  "force": true
}
```

**响应：**
```json
{
  "filesProcessed": 5,
  "chunksCreated": 123,
  "duration": 1500,
  "errors": []
}
```

### GET /api/readfile

读取文件内容

**查询参数：**
- `path`: 文件路径（必需）
- `lineStart`: 起始行号（可选）
- `lines`: 读取行数（可选）

**响应：**
```json
{
  "content": "文件内容...",
  "path": "ppe.md",
  "lineStart": 1,
  "lineEnd": 50
}
```

## Docker 部署

### 构建镜像

```bash
docker build -t rag-memory-service .
```

### 运行容器

```bash
docker run -d \
  -p 3001:3001 \
  -e ZHIPU_API_KEY=your_key \
  -v $(pwd)/knowledge_docs:/app/knowledge_docs \
  -v rag-memory-data:/app/data \
  rag-memory-service
```

## 目录结构

```
rag_memory_service/
├── src/
│   ├── server.ts           # Express 服务器入口
│   ├── routes/
│   │   ├── search.ts       # 搜索路由
│   │   ├── sync.ts         # 同步路由
│   │   ├── status.ts       # 状态路由
│   │   └── readfile.ts     # 文件读取路由
│   ├── middleware/
│   │   ├── errorHandler.ts # 错误处理
│   │   └── logger.ts       # 请求日志
│   └── config/
│       └── index.ts        # 配置管理
├── knowledge_docs/         # Markdown 文档目录
├── data/                   # SQLite 索引目录
├── package.json
├── tsconfig.json
└── Dockerfile
```

## 开发说明

### 添加新依赖

```bash
npm install <package>
npm install --save-dev <package>@types/<package>
```

### 重新构建

```bash
npm run build
```

### 测试 API

```bash
# 搜索测试
curl -X POST http://localhost:3001/api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"安全帽","limit":5}'

# 状态查询
curl http://localhost:3001/api/status

# 同步索引
curl -X POST http://localhost:3001/api/sync
```

## 故障排除

### 1. ZHIPU_API_KEY 未设置

错误：`ZHIPU_API_KEY environment variable is required`

解决：在 `.env` 文件中设置有效的 API 密钥

### 2. 文档目录为空

警告：`No documents found in documents path`

解决：将 Markdown 文档放入 `knowledge_docs/` 目录

### 3. 端口已被占用

错误：`Error: listen EADDRINUSE: address already in use`

解决：修改 `.env` 中的 `PORT` 或停止占用端口的进程

## 许可证

MIT
