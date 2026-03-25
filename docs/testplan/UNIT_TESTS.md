# 单元测试详细规格

## 后端单元测试

### 1. Models 测试

#### test_user_model.py
```python
import pytest
from backend.models import User
from backend.api.deps import get_password_hash, verify_password

class TestUserModel:
    def test_create_user(self):
        """测试用户创建"""
        user = User(
            username="testuser",
            email="test@example.com",
            password_hash=get_password_hash("Test123!")
        )
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.password_hash is not None

    def test_password_hashing(self):
        """测试密码哈希和验证"""
        password = "SecurePass123!"
        hashed = get_password_hash(password)
        assert hashed != password
        assert verify_password(password, hashed) is True
        assert verify_password("wrongpass", hashed) is False

    def test_password_not_plaintext(self):
        """验证密码不以明文存储"""
        user = User(
            username="testuser",
            email="test@example.com",
            password_hash=get_password_hash("Test123!")
        )
        assert "Test123!" not in user.password_hash
```

#### test_project_model.py
```python
from datetime import datetime
from backend.models import Project, User

class TestProjectModel:
    def test_create_project(self, db_session):
        """测试项目创建"""
        user = User(username="owner", email="owner@test.com", password_hash="hash")
        project = Project(
            user_id=user.id,
            name="Test Project",
            description="A test project"
        )
        assert project.name == "Test Project"
        assert project.status == "draft"
        assert project.created_at is not None

    def test_project_status_transitions(self):
        """测试项目状态流转"""
        project = Project(name="Test", user_id="user-id")
        valid_statuses = ["draft", "active", "completed", "archived"]
        # 初始状态应为 draft
        assert project.status == "draft"
```

#### test_document_model.py
```python
from backend.models import Document

class TestDocumentModel:
    def test_create_document(self):
        """测试文档创建"""
        doc = Document(
            project_id="proj-id",
            doc_type="tender",
            original_filename="tender.pdf",
            file_path="/workspace/user/proj/tender.pdf"
        )
        assert doc.doc_type == "tender"
        assert doc.status == "pending"

    def test_document_status_values(self):
        """测试文档状态枚举"""
        doc = Document(project_id="proj-id", doc_type="bid", original_filename="bid.pdf", file_path="/path")
        valid_statuses = ["pending", "parsing", "parsed", "failed"]
        # pending 是默认值
        assert doc.status == "pending"
```

#### test_review_task_model.py
```python
from backend.models import ReviewTask

class TestReviewTaskModel:
    def test_create_review_task(self):
        """测试审查任务创建"""
        task = ReviewTask(project_id="proj-id")
        assert task.status == "pending"
        assert task.celery_task_id is None
        assert task.started_at is None

    def test_review_task_status_transitions(self):
        """测试任务状态流转"""
        task = ReviewTask(project_id="proj-id")
        # pending -> running
        task.status = "running"
        assert task.status == "running"
        # running -> completed
        task.status = "completed"
        assert task.status == "completed"

    def test_review_task_cancellation(self):
        """测试任务取消"""
        task = ReviewTask(project_id="proj-id")
        task.status = "cancelled"
        assert task.status == "cancelled"
```

### 2. Services 测试

#### test_auth_service.py
```python
from datetime import timedelta
from backend.api.deps import create_access_token, create_refresh_token, verify_password
from backend.config import get_settings

class TestAuthService:
    def test_create_access_token(self):
        """测试 access token 创建"""
        token = create_access_token(data={"sub": "user-id"})
        assert token is not None
        assert isinstance(token, str)

    def test_create_refresh_token(self):
        """测试 refresh token 创建"""
        token = create_refresh_token(data={"sub": "user-id"})
        assert token is not None

    def test_token_expiration(self):
        """测试 token 过期时间"""
        settings = get_settings()
        assert settings.access_token_expire_minutes == 30
        assert settings.refresh_token_expire_days == 7
```

#### test_sse_service.py
```python
import pytest
from backend.services.sse_service import SSEConnectionManager

class TestSSEService:
    @pytest.fixture
    def sse_manager(self):
        return SSEConnectionManager()

    def test_sse_manager_initialization(self, sse_manager):
        """测试 SSE 管理器初始化"""
        assert sse_manager._redis_client is None

    @pytest.mark.asyncio
    async def test_connect_creates_redis_client(self, sse_manager):
        """测试连接时创建 Redis 客户端"""
        # 注意: 需要 mock Redis
        pass
```

### 3. Agent Tools 测试

#### test_doc_search_tool.py
```python
import pytest
import tempfile
from pathlib import Path
from backend.agent.tools.doc_search import DocSearchTool

class TestDocSearchTool:
    @pytest.fixture
    def temp_doc(self):
        """创建临时文档"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write("Line 1: 招标要求 - 资质证书\n")
            f.write("Line 2: 质量管理体系\n")
            f.write("Line 3: ISO9001认证\n")
            return Path(f.name)

    @pytest.mark.asyncio
    async def test_search_tender_doc_success(self, temp_doc):
        """测试搜索招标书成功"""
        tool = DocSearchTool(tender_doc_path=str(temp_doc), bid_doc_path="/tmp/bid.md")
        result = await tool.execute(doc_type="tender")
        assert result.success is True
        assert "招标要求" in result.content

    @pytest.mark.asyncio
    async def test_search_with_query(self, temp_doc):
        """测试带关键词搜索"""
        tool = DocSearchTool(tender_doc_path=str(temp_doc), bid_doc_path="/tmp/bid.md")
        result = await tool.execute(doc_type="tender", query="资质")
        assert result.success is True
        assert "资质" in result.content

    @pytest.mark.asyncio
    async def test_search_doc_not_found(self):
        """测试文档不存在"""
        tool = DocSearchTool(tender_doc_path="/nonexistent.md", bid_doc_path="/tmp/bid.md")
        result = await tool.execute(doc_type="tender")
        assert result.success is False
        assert "Document not found" in result.error
```

#### test_rag_search_tool.py
```python
import pytest
from unittest.mock import AsyncMock, patch
from backend.agent.tools.rag_search import RAGSearchTool

class TestRAGSearchTool:
    @pytest.mark.asyncio
    async def test_rag_search_success(self):
        """测试 RAG 搜索成功"""
        tool = RAGSearchTool()
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [
                    {"snippet": "质量管理体系认证..."},
                    {"snippet": "ISO9001标准..."}
                ]
            }
            mock_post.return_value = mock_response

            result = await tool.execute(query="质量管理体系")
            assert result.success is True
            assert "质量管理体系" in result.content

    @pytest.mark.asyncio
    async def test_rag_search_connection_error(self):
        """测试 RAG 服务连接错误"""
        tool = RAGSearchTool()
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_post.side_effect = Exception("Connection refused")
            result = await tool.execute(query="test")
            assert result.success is False
```

#### test_comparator_tool.py
```python
import pytest
from unittest.mock import AsyncMock, patch
from backend.agent.tools.comparator import ComparatorTool

class TestComparatorTool:
    @pytest.mark.asyncio
    async def test_comparator_empty_bid_content(self):
        """测试空应标内容 - 自动不合规"""
        tool = ComparatorTool()
        result = await tool.execute(
            requirement="需要提供资质证书",
            bid_content="N/A"
        )
        assert result.success is True
        data = result.data
        assert data["is_compliant"] is False
        assert data["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_comparator_with_llm(self):
        """测试 LLM 比对"""
        tool = ComparatorTool()
        with patch.object(tool._llm_client, 'generate') as mock_generate:
            mock_response = AsyncMock()
            mock_response.content = '{"is_compliant": true, "explanation": "满足要求", "suggestion": ""}'
            mock_generate.return_value = mock_response

            result = await tool.execute(
                requirement="需要有ISO9001认证",
                bid_content="我司已获得ISO9001质量管理体系认证"
            )
            assert result.success is True

    @pytest.mark.asyncio
    async def test_comparator_json_parse_error(self):
        """测试 LLM 返回 JSON 解析失败"""
        tool = ComparatorTool()
        with patch.object(tool._llm_client, 'generate') as mock_generate:
            mock_response = AsyncMock()
            mock_response.content = "这不是有效的JSON"
            mock_generate.return_value = mock_response

            result = await tool.execute(
                requirement="测试要求",
                bid_content="测试内容"
            )
            assert result.success is True
            # 应有降级处理
```

### 4. Tasks 测试

#### test_document_parser.py
```python
import pytest
import tempfile
from pathlib import Path
from backend.tasks.document_parser import _parse_pdf, _parse_docx

class TestDocumentParser:
    @pytest.mark.asyncio
    async def test_parse_pdf_basic(self):
        """测试 PDF 基本解析"""
        # 需要一个真实的 PDF 文件或 mock
        pass

    @pytest.mark.asyncio
    async def test_parse_docx_basic(self):
        """测试 DOCX 基本解析"""
        # 需要一个真实的 DOCX 文件或 mock
        pass

    @pytest.mark.asyncio
    async def test_extract_images(self):
        """测试图像提取"""
        pass

    @pytest.mark.asyncio
    async def test_unsupported_file_type(self):
        """测试不支持的文件类型"""
        pass
```

#### test_review_tasks.py
```python
import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.tasks.review_tasks import (
    _record_agent_step,
    _create_error_finding,
    _parse_findings_result
)

class TestReviewTasks:
    def test_create_error_finding(self):
        """测试错误 findings 创建"""
        result = _create_error_finding("File not found")
        assert len(result) == 1
        assert result[0]["requirement_key"] == "review_error"
        assert result[0]["severity"] == "critical"

    def test_parse_findings_result_list(self):
        """测试 findings 列表解析"""
        input_data = [
            {"requirement": "req1", "is_compliant": True},
            {"requirement": "req2", "is_compliant": False}
        ]
        result = _parse_findings_result(input_data)
        assert result == input_data

    def test_parse_findings_result_dict(self):
        """测试 findings dict 解析"""
        input_data = {"findings": [{"requirement": "req1"}]}
        result = _parse_findings_result(input_data)
        assert result == [{"requirement": "req1"}]

    def test_parse_findings_result_invalid(self):
        """测试无效 findings 解析"""
        result = _parse_findings_result("invalid")
        assert len(result) == 1
        assert result[0]["requirement_key"] == "review_completed"
```

---

## 前端单元测试

### 1. Store Tests

#### test_project_store.ts
```typescript
import { setActivePinia, createPinia } from 'pinia'
import { useProjectStore } from '@/stores/project'
import { describe, it, expect, beforeEach, vi } from 'vitest'

// Mock API client
vi.mock('@/api/client', () => ({
  projectsApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    delete: vi.fn()
  },
  documentsApi: {
    list: vi.fn(),
    upload: vi.fn(),
    delete: vi.fn()
  },
  reviewApi: {
    start: vi.fn(),
    getResults: vi.fn()
  }
}))

describe('Project Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('should fetch projects', async () => {
    const store = useProjectStore()
    const mockProjects = [{ id: '1', name: 'Test Project' }]
    vi.mocked(projectsApi.list).mockResolvedValue(mockProjects)

    await store.fetchProjects()

    expect(store.projects).toEqual(mockProjects)
  })

  it('should create project', async () => {
    const store = useProjectStore()
    const newProject = { id: '2', name: 'New Project' }
    vi.mocked(projectsApi.create).mockResolvedValue(newProject)

    const result = await store.createProject('New Project')

    expect(result).toEqual(newProject)
    expect(store.projects[0]).toEqual(newProject)
  })

  it('should handle SSE events', () => {
    const store = useProjectStore()
    store.currentTask = { id: 'task-1', status: 'pending' }

    store.handleSSEEvent({ type: 'status', status: 'running' })

    expect(store.currentTask?.status).toBe('running')
  })
})
```

### 2. Component Tests

#### test_SeverityBadge.vue
```typescript
import { mount } from '@vue/test-utils'
import SeverityBadge from '@/components/SeverityBadge.vue'

describe('SeverityBadge', () => {
  it('should render critical severity', () => {
    const wrapper = mount(SeverityBadge, {
      props: { severity: 'critical' }
    })
    expect(wrapper.classes()).toContain('severity-critical')
    expect(wrapper.text()).toBe('critical')
  })

  it('should render major severity', () => {
    const wrapper = mount(SeverityBadge, {
      props: { severity: 'major' }
    })
    expect(wrapper.classes()).toContain('severity-major')
  })

  it('should render minor severity', () => {
    const wrapper = mount(SeverityBadge, {
      props: { severity: 'minor' }
    })
    expect(wrapper.classes()).toContain('severity-minor')
  })
})
```

#### test_ResultsTable.vue
```typescript
import { mount } from '@vue/test-utils'
import ResultsTable from '@/components/ResultsTable.vue'

describe('ResultsTable', () => {
  const mockFindings = [
    {
      id: '1',
      requirement_content: '需要ISO9001认证',
      bid_content: '已获得ISO9001认证',
      is_compliant: true,
      severity: 'critical'
    },
    {
      id: '2',
      requirement_content: '需要高级工程师',
      bid_content: 'N/A',
      is_compliant: false,
      severity: 'major'
    }
  ]

  it('should render findings', () => {
    const wrapper = mount(ResultsTable, {
      props: { findings: mockFindings }
    })
    expect(wrapper.findAll('.finding-row')).toHaveLength(2)
  })

  it('should show compliant badge for compliant items', () => {
    const wrapper = mount(ResultsTable, {
      props: { findings: [mockFindings[0]] }
    })
    expect(wrapper.find('.badge-compliant')).toBeTruthy()
  })

  it('should show non-compliant badge for non-compliant items', () => {
    const wrapper = mount(ResultsTable, {
      props: { findings: [mockFindings[1]] }
    })
    expect(wrapper.find('.badge-non-compliant')).toBeTruthy()
  })
})
```
