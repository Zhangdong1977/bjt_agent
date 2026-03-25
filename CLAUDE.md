# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Bid Review Agent System** (标书审查智能体) - a Vue3/FastAPI application that compares tender documents (招标书) against bid documents (应标书) to identify non-compliant items using a Mini-Agent-based review agent.

## Architecture

```
Frontend (Vue3) ──SSE──► FastAPI (Backend) ──► Celery Workers ──► BidReviewAgent
                                   │                    │
                                   ▼                    ▼
                              PostgreSQL            Redis (broker + SSE)
                                   │
                        rag_memory_service (external)
                                   │
                              Mini-Max API (LLM + Image Understanding)
```

### Backend Structure

- **API Layer** (`backend/api/`): FastAPI routers for auth, projects, documents, review
- **Service Layer** (`backend/services/`): Business logic, SSE event management via `sse_service`
- **Agent Layer** (`backend/agent/`): `BidReviewAgent` extends Mini-Agent with 3 custom tools:
  - `DocSearchTool` - reads parsed tender/bid markdown documents
  - `RAGSearchTool` - queries enterprise knowledge base (rag_memory_service)
  - `ComparatorTool` - compares requirements against bid content
- **Task Layer** (`backend/tasks/`): Celery tasks for async document parsing and review execution
- **Models** (`backend/models/`): SQLAlchemy models with async support

### Key Configuration

- Database: PostgreSQL at `183.66.37.186:7004` (configured in `backend/config.py`)
- Redis: `183.66.37.186:7005` (Celery broker + SSE pub/sub)
- Mini-Agent API: `https://api.minimaxi.com` with model `MiniMax-M2.7-highspeed`
- Workspace: `./workspace/{user_id}/{project_id}/` for document storage

## Commands

### Backend Development
```bash
cd backend
# Start API server with hot reload
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Run Celery workers (two queues: review and parser)
celery -A celery_app worker --loglevel=info -Q review
celery -A celery_app worker --loglevel=info -Q parser

# Run tests
pytest tests/ -v
# Run a specific test file
pytest tests/test_auth.py -v
```

### Frontend Development
```bash
cd frontend
npm run dev      # Development server on port 3000
npm run build    # Production build (requires vue-tsc type checking)
```

### Service Management
```bash
# Use the unified management script (recommended)
./scripts/bjt.sh start    # Start all services
./scripts/bjt.sh stop     # Stop all services
./scripts/bjt.sh status   # Check service status
./scripts/bjt.sh restart  # Restart all services
```

## Mini-Agent Submodule

The project uses `Mini-Agent` as a git submodule at `./Mini-Agent/`. It provides the base `Agent` class that `BidReviewAgent` extends. The submodule path is added to `sys.path` in `backend/agent/bid_review_agent.py`.

## SSE Event Flow

Real-time updates flow through Redis pub/sub:
1. Celery task publishes events to `task:{task_id}` channel
2. Backend SSE endpoint (`/api/events/tasks/{task_id}/stream`) subscribes
3. Frontend EventSource receives events for timeline display

## Document Storage

Documents are stored in workspace directories:
- `{workspace}/{user_id}/{project_id}/tender.pdf|md`
- `{workspace}/{user_id}/{project_id}/bid.pdf|md`
- Parsed content and images in corresponding `_parsed.md` and `_images/` paths

## Testing

Tests are in `backend/tests/` and require a running backend server. They use `pytest-asyncio` with `async_mode = "auto"` (configured in `pyproject.toml`). Test fixtures in `conftest.py` create unique users per test function.

## Chrome DevTools Usage

### 进程管理注意事项

**禁止使用 `pkill -f "chrome"` 或类似命令按 chrome 关键词杀进程。**

Claude Code 进程名包含 "chrome" 关键字，`pkill -f "chrome"` 会无差别匹配并杀死 Claude Code 本身。

杀 Chrome 进程时，必须使用更精确的条件：
- 用 `--remote-debugging-port=9222` 端口号匹配
- 或用完整路径 `/usr/bin/google-chrome` 匹配
- 或用 `headless` 等特定参数匹配

### VNC 远程桌面

Chrome DevTools 调试需在 VNC 远程桌面的 DISPLAY=:2 上运行，不要用headless模式。

启动 Chrome 时使用 `DISPLAY=:2 google-chrome --remote-debugging-port=9222 ...` 确保运行在 VNC 的 DISPLAY=:2 上。
