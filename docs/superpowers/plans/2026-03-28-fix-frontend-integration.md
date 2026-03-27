# 修复前端集成问题实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复前端页面与后端API的集成问题，确保三个新模块（标书检查、历史标书、知识库）能正确运行。

**Architecture:** 后端新增知识库API路由，前端修复HistoryView的status过滤问题，验证各模块与现有API的集成。

**Tech Stack:** FastAPI (Python), Vue 3, Ant Design Vue 4, SQLAlchemy

---

## 问题分析

### 1. 知识库API不存在
前端 `knowledgeApi` 调用以下不存在的端点：
- `GET /knowledge/documents` - 获取文档列表
- `POST /knowledge/upload` - 上传文档
- `DELETE /knowledge/documents/{id}` - 删除文档
- `GET /knowledge/{id}/preview` - 预览文档

### 2. HistoryView错误过滤
`Project` 类型没有 `status` 字段，但 HistoryView 尝试按 `status` 过滤项目。

### 3. 现有API已完善
后端已有：
- Projects API: CRUD操作
- Documents API: 项目文档管理
- Review API: 审查任务管理

---

## 任务清单

### Task 1: 创建后端知识库API

**Files:**
- Create: `backend/api/knowledge.py` - 知识库API路由
- Modify: `backend/api/__init__.py` - 注册新路由
- Create: `backend/schemas/knowledge.py` - Pydantic schemas

**Step 1: 创建知识库API路由**

在 `backend/api/knowledge.py` 创建:

```python
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from typing import List
import os
import shutil
from datetime import datetime
from uuid import uuid4

from .deps import get_current_user
from models.user import User
from schemas.knowledge import KnowledgeDocumentResponse, KnowledgeDocumentListResponse

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# 知识库存储路径
KNOWLEDGE_BASE_DIR = os.environ.get("KNOWLEDGE_BASE_DIR", "./workspace/knowledge")

@router.get("/documents", response_model=KnowledgeDocumentListResponse)
async def list_documents(current_user: User = Depends(get_current_user)):
    """获取当前用户的所有知识库文档"""
    user_dir = os.path.join(KNOWLEDGE_BASE_DIR, current_user.id)
    if not os.path.exists(user_dir):
        return KnowledgeDocumentListResponse(documents=[], total=0)

    documents = []
    for item in os.listdir(user_dir):
        item_path = os.path.join(user_dir, item)
        if os.path.isfile(item_path):
            stat = os.stat(item_path)
            documents.append(KnowledgeDocumentResponse(
                id=item,
                filename=item,
                file_path=item_path,
                file_size=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_ctime),
                file_type=os.path.splitext(item)[1]
            ))

    return KnowledgeDocumentListResponse(documents=documents, total=len(documents))

@router.post("/upload", response_model=KnowledgeDocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """上传知识库文档"""
    user_dir = os.path.join(KNOWLEDGE_BASE_DIR, current_user.id)
    os.makedirs(user_dir, exist_ok=True)

    # 生成唯一文件名
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = os.path.join(user_dir, unique_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    stat = os.stat(file_path)
    return KnowledgeDocumentResponse(
        id=unique_filename,
        filename=file.filename,
        file_path=file_path,
        file_size=stat.st_size,
        created_at=datetime.fromtimestamp(stat.st_ctime),
        file_type=file_ext
    )

@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user)
):
    """删除知识库文档"""
    user_dir = os.path.join(KNOWLEDGE_BASE_DIR, current_user.id)
    file_path = os.path.join(user_dir, document_id)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Document not found")

    # 安全检查：确保文件在用户目录下
    if not file_path.startswith(os.path.abspath(user_dir)):
        raise HTTPException(status_code=403, detail="Access denied")

    os.remove(file_path)
    return {"message": "Document deleted"}

@router.get("/documents/{document_id}/preview")
async def preview_document(
    document_id: str,
    current_user: User = Depends(get_current_user)
):
    """预览知识库文档"""
    user_dir = os.path.join(KNOWLEDGE_BASE_DIR, current_user.id)
    file_path = os.path.join(user_dir, document_id)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Document not found")

    # 安全检查
    if not file_path.startswith(os.path.abspath(user_dir)):
        raise HTTPException(status_code=403, detail="Access denied")

    # 对于图片直接返回，对于PDF返回路径（前端处理）
    file_ext = os.path.splitext(file_path)[1].lower()
    if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        from fastapi.responses import FileResponse
        return FileResponse(file_path)
    else:
        # 返回文件路径让前端处理
        return {"file_path": file_path, "filename": os.path.basename(file_path)}
```

**Step 2: 创建Pydantic Schemas**

在 `backend/schemas/knowledge.py` 创建:

```python
from pydantic import BaseModel
from datetime import datetime
from typing import List

class KnowledgeDocumentResponse(BaseModel):
    id: str
    filename: str
    file_path: str
    file_size: int
    created_at: datetime
    file_type: str

    class Config:
        from_attributes = True

class KnowledgeDocumentListResponse(BaseModel):
    documents: List[KnowledgeDocumentResponse]
    total: int
```

**Step 3: 注册路由**

修改 `backend/api/__init__.py`:

```python
from .knowledge import router as knowledge_router

__all__ = [
    "auth_router",
    "projects_router",
    "documents_router",
    "review_router",
    "knowledge_router",  # 新增
]
```

修改 `backend/main.py` (或 `backend/api/__init__.py` 中注册路由的地方)，添加 `knowledge_router` 到主应用。

**Step 4: 提交**

```bash
git add backend/api/knowledge.py backend/schemas/knowledge.py backend/api/__init__.py
git commit -m "feat: add knowledge base API for document management"
```

---

### Task 2: 修复HistoryView状态过滤

**Files:**
- Modify: `frontend/src/views/HistoryView.vue`

**问题:** `Project` 类型没有 `status` 字段，但 HistoryView 尝试按 `status` 过滤。

**解决方案:** 移除 status 过滤，因为项目本身没有状态。项目的状态应该通过其关联的 ReviewTask 或 Document 来确定。

**Step 1: 修改HistoryView.vue**

```vue
<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { message } from 'ant-design-vue'

const router = useRouter()
const projectStore = useProjectStore()

const searchText = ref('')

onMounted(() => {
  projectStore.fetchProjects()
})

const filteredProjects = computed(() => {
  let projects = projectStore.projects

  if (searchText.value) {
    const keyword = searchText.value.toLowerCase()
    projects = projects.filter(p =>
      p.name.toLowerCase().includes(keyword)
    )
  }

  return projects
})

function goToProject(projectId: string) {
  router.push({ name: 'project', params: { id: projectId } })
}

async function deleteProject(projectId: string, event: Event) {
  event.stopPropagation()
  if (confirm('确定要删除此项目吗？')) {
    await projectStore.deleteProject(projectId)
    message.success('项目已删除')
  }
}
</script>

<template>
  <div class="history-view">
    <a-breadcrumb class="breadcrumb">
      <a-breadcrumb-item>首页</a-breadcrumb-item>
      <a-breadcrumb-item>历史标书</a-breadcrumb-item>
    </a-breadcrumb>

    <a-card class="filter-card" :bordered="false">
      <div class="filters">
        <a-input-search
          v-model:value="searchText"
          placeholder="搜索项目名称"
          style="width: 300px"
          allow-clear
        />
      </div>
    </a-card>

    <a-card class="list-card" :bordered="false">
      <a-table
        :dataSource="filteredProjects"
        :columns="[
          { title: '项目名称', dataIndex: 'name', key: 'name' },
          { title: '描述', dataIndex: 'description', key: 'description' },
          { title: '创建时间', dataIndex: 'created_at', key: 'created_at' },
          { title: '操作', key: 'action' }
        ]"
        :pagination="{ pageSize: 10 }"
        row-key="id"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <a @click="goToProject(record.id)" class="project-link">
              {{ record.name }}
            </a>
          </template>
          <template v-else-if="column.key === 'description'">
            {{ record.description || '-' }}
          </template>
          <template v-else-if="column.key === 'created_at'">
            {{ new Date(record.created_at).toLocaleDateString() }}
          </template>
          <template v-else-if="column.key === 'action'">
            <a-space>
              <a @click="goToProject(record.id)">查看详情</a>
              <a-divider type="vertical" />
              <a-popconfirm
                title="确定要删除此项目吗？"
                @confirm="deleteProject(record.id, $event)"
              >
                <a class="delete-link">删除</a>
              </a-popconfirm>
            </a-space>
          </template>
        </template>

        <template #emptyText>
          <a-empty description="暂无历史项目" />
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<style scoped>
.history-view {
  max-width: 1200px;
  margin: 0 auto;
}

.breadcrumb {
  margin-bottom: 24px;
}

.filter-card,
.list-card {
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  margin-bottom: 24px;
}

.filters {
  display: flex;
  gap: 16px;
  align-items: center;
}

.project-link {
  color: #6366f1;
  font-weight: 500;
}

.project-link:hover {
  color: #4f46e5;
}

.delete-link {
  color: #ff4d4f;
}

.delete-link:hover {
  color: #d9363e;
}
</style>
```

**Step 2: 提交**

```bash
git add frontend/src/views/HistoryView.vue
git commit -m "fix: remove invalid status filter from HistoryView"
```

---

### Task 3: 验证前端构建

**Step 1: 运行前端构建**

```bash
cd frontend && npm run build
```

**Step 2: 如果有错误，修复它们**

常见问题：
- TypeScript类型错误
- 缺失的导入
- API端点不匹配

**Step 3: 提交修复**

```bash
git add -A
git commit -m "fix: resolve frontend build errors"
```

---

### Task 4: 验证后端运行

**Step 1: 检查后端能否启动**

```bash
cd backend
# 使用ssirs conda环境
source ~/miniconda3/etc/profile.d/conda.sh
conda activate ssirs
python -c "from main import app; print('Backend imports OK')"
```

**Step 2: 启动后端验证API端点存在**

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

在另一个终端测试:
```bash
curl http://localhost:8000/api/knowledge/documents
```

**Step 3: 提交**

```bash
git add -A
git commit -m "chore: verify backend runs correctly"
```

---

## 实施检查清单

- [ ] Task 1: 后端知识库API创建
- [ ] Task 2: HistoryView修复
- [ ] Task 3: 前端构建验证
- [ ] Task 4: 后端运行验证

---

## Task 5: 系统联调测试 (测试团队执行)

**测试工具**: Chrome DevTools (VNC远程桌面, DISPLAY=:2)

**测试步骤**:

### 5.1 启动服务
```bash
# 启动后端 (在backend目录)
cd /home/openclaw/bjt_agent/backend
conda activate ssirs
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 启动前端 (在frontend目录)
cd /home/openclaw/bjt_agent/frontend
npm run dev
```

### 5.2 登录测试
1. 打开Chrome浏览器 (DISPLAY=:2)
2. 访问 `http://localhost:3000`
3. 使用测试账号登录
4. 验证登录成功，跳转到 `/home/check`

### 5.3 标书检查模块测试
1. 点击"标书检查"菜单
2. 验证显示创建项目表单
3. 输入项目名称，点击创建
4. 验证跳转到项目详情页

### 5.4 历史标书模块测试
1. 点击"历史标书"菜单
2. 验证显示项目列表
3. 测试搜索功能
4. 点击项目进入详情

### 5.5 知识库模块测试
1. 点击"知识库"菜单
2. 验证显示上传区域
3. 上传一个测试文档
4. 验证文档出现在列表中
5. 测试删除功能

### 5.6 Chrome DevTools调试
1. 打开DevTools (F12)
2. Network标签: 检查API请求是否正确
3. Console标签: 检查是否有JavaScript错误
4. 验证API响应状态码

**预期结果**:
- [ ] 登录成功
- [ ] 标书检查模块可创建项目
- [ ] 历史标书模块可显示项目列表
- [ ] 知识库模块可上传/删除文档
- [ ] 所有API请求无404/500错误
- [ ] Console无JavaScript错误

---

## 实施检查清单

- [ ] Task 1: 后端知识库API创建
- [ ] Task 2: HistoryView修复
- [ ] Task 3: 前端构建验证
- [ ] Task 4: 后端运行验证
- [ ] Task 5: 系统联调测试 (测试团队)

---

## 预期结果

完成所有任务后:
1. 知识库API端点可用 (`/api/knowledge/*`)
2. HistoryView不再有错误的状态过滤
3. 前端构建通过
4. 前后端可以正常通信
5. 系统联调测试通过
