# RAG 多租户隔离设计

**Date:** 2026-03-29
**Status:** Approved

## Goal

将 RAG 索引从单租户改为多租户架构，每个用户的文档索引互相独立。

## Current Problem

- RAG service 使用单一 SQLite 索引文件
- `GET /api/status` 返回全局索引状态（所有用户共享）
- `POST /api/search` 返回所有用户的搜索结果
- 前端显示"索引就绪 3 个文件, 19 个分片"但文档列表只有 1 个文档

## Architecture

```
FastAPI (Python)              RAG Service (Node.js)
      │                            │
      │ POST /knowledge/search     │
      │ X-User-ID: <uid>          │
      │ ──────────────────────────►
      │                            │ Load index:
      │                            │ workspace/knowledge/{uid}/data/memory.sqlite
      │                            │
      │◄───────────────────────────
      │   Filtered results        │
```

## Core Changes

### 1. RAG Service — Per-User Index Loading

**New index path pattern:**
```
workspace/knowledge/{user_id}/data/memory.sqlite
```

**Internal MemoryIndex cache:**
```typescript
Map<user_id, MemoryIndex>
```

**Modified endpoints:**

#### `POST /api/search`
- Read `X-User-ID` header
- Load or create MemoryIndex for that user
- Search in user's index only

#### `GET /api/status`
- Read `X-User-ID` header
- Return status for user's index only

#### `POST /api/sync`
- Read `X-User-ID` header
- Sync documents from `workspace/knowledge/{user_id}/`
- Index to user's SQLite file

### 2. FastAPI — Proxy with User Header

**Modified endpoints:**

#### `POST /knowledge/search`
- Extract `user_id` from JWT token
- Forward request to RAG service with `X-User-ID: {user_id}` header
- Verify request `user_id` matches token (security check)

#### `GET /knowledge/index-status`
- Extract `user_id` from JWT token
- Forward to RAG service with `X-User-ID` header

#### Upload/Delete triggers sync
- Pass `X-User-ID` header when calling `POST /api/sync`

## Data Flow

1. **Upload:** User uploads doc → FastAPI saves to `workspace/knowledge/{user_id}/doc.docx`
2. **Sync trigger:** FastAPI calls `POST /api/sync` with `X-User-ID: {user_id}`
3. **Index:** RAG service scans user's dir, converts docs to markdown, indexes to user's SQLite
4. **Search:** User searches → RAG loads user's index → returns filtered results

## File Structure

```
workspace/knowledge/
  {user_id_1}/
    doc1.docx
    doc1.docx.md
    data/
      memory.sqlite      # User 1's index
  {user_id_2}/
    doc2.docx
    doc2.docx.md
    data/
      memory.sqlite      # User 2's index
```

## API Changes

### RAG Service

| Endpoint | Change |
|----------|--------|
| `POST /api/search` | Require `X-User-ID` header, search in user's index |
| `GET /api/status` | Require `X-User-ID` header, return user's index status |
| `POST /api/sync` | Require `X-User-ID` header, sync user's documents |

### FastAPI

| Endpoint | Change |
|----------|--------|
| `POST /knowledge/search` | Add `X-User-ID` header when calling RAG |
| `GET /knowledge/index-status` | Add `X-User-ID` header when calling RAG |

## Security

- FastAPI validates `user_id` in JWT token matches the request
- RAG service only serves data for the `X-User-ID` provided
- Users cannot access other users' indices

## Implementation Notes

- On first search for a user, create new MemoryIndex instance
- Cache MemoryIndex instances in memory (Map)
- Close index on app shutdown or cache eviction
- Document parser remains unchanged
