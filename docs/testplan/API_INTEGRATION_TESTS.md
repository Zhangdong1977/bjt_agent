# API 集成测试规格

## 测试环境设置

```python
import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.models import init_db, close_db, async_session_factory
from sqlalchemy import select

@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环供所有测试使用"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def client():
    """创建测试客户端"""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()

@pytest.fixture
async def auth_token(client: AsyncClient):
    """创建测试用户并返回 token"""
    # 注册用户
    await client.post("/api/auth/register", json={
        "username": "testuser",
        "email": "test@example.com",
        "password": "Test123!"
    })
    # 登录获取 token
    response = await client.post("/api/auth/login", data={
        "username": "testuser",
        "password": "Test123!"
    })
    return response.json()["access_token"]

@pytest.fixture
async def auth_headers(auth_token):
    """返回认证 headers"""
    return {"Authorization": f"Bearer {auth_token}"}
```

## 认证 API 测试

### POST /api/auth/register

```python
class TestAuthRegister:
    """用户注册 API 测试"""

    async def test_register_success(self, client: AsyncClient):
        """注册成功"""
        response = await client.post("/api/auth/register", json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "SecurePass123!"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "newuser"
        assert data["email"] == "newuser@example.com"
        assert "id" in data
        assert "password" not in data

    async def test_register_duplicate_username(self, client: AsyncClient):
        """用户名重复"""
        # 先注册一个用户
        await client.post("/api/auth/register", json={
            "username": "duplicate",
            "email": "first@example.com",
            "password": "Test123!"
        })
        # 尝试重复注册
        response = await client.post("/api/auth/register", json={
            "username": "duplicate",
            "email": "second@example.com",
            "password": "Test123!"
        })
        assert response.status_code == 400
        assert "Username already registered" in response.json()["detail"]

    async def test_register_duplicate_email(self, client: AsyncClient):
        """邮箱重复"""
        await client.post("/api/auth/register", json={
            "username": "user1",
            "email": "same@example.com",
            "password": "Test123!"
        })
        response = await client.post("/api/auth/register", json={
            "username": "user2",
            "email": "same@example.com",
            "password": "Test123!"
        })
        assert response.status_code == 400
        assert "Email already registered" in response.json()["detail"]

    async def test_register_invalid_email(self, client: AsyncClient):
        """无效邮箱格式"""
        response = await client.post("/api/auth/register", json={
            "username": "testuser",
            "email": "not-an-email",
            "password": "Test123!"
        })
        assert response.status_code == 422

    async def test_register_short_password(self, client: AsyncClient):
        """密码过短"""
        response = await client.post("/api/auth/register", json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "short"
        })
        assert response.status_code == 422
```

### POST /api/auth/login

```python
class TestAuthLogin:
    """用户登录 API 测试"""

    async def test_login_success(self, client: AsyncClient):
        """登录成功"""
        # 先注册
        await client.post("/api/auth/register", json={
            "username": "loginuser",
            "email": "login@example.com",
            "password": "Test123!"
        })
        # 登录
        response = await client.post("/api/auth/login", data={
            "username": "loginuser",
            "password": "Test123!"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(self, client: AsyncClient):
        """密码错误"""
        await client.post("/api/auth/register", json={
            "username": "passuser",
            "email": "pass@example.com",
            "password": "CorrectPass123!"
        })
        response = await client.post("/api/auth/login", data={
            "username": "passuser",
            "password": "WrongPass123!"
        })
        assert response.status_code == 401
        assert "Incorrect email or password" in response.json()["detail"]

    async def test_login_nonexistent_user(self, client: AsyncClient):
        """用户不存在"""
        response = await client.post("/api/auth/login", data={
            "username": "nonexistent",
            "password": "Test123!"
        })
        assert response.status_code == 401
```

### POST /api/auth/refresh

```python
class TestAuthRefresh:
    """Token 刷新 API 测试"""

    async def test_refresh_token_success(self, client: AsyncClient):
        """刷新成功"""
        # 注册并登录
        await client.post("/api/auth/register", json={
            "username": "refreshuser",
            "email": "refresh@example.com",
            "password": "Test123!"
        })
        login_response = await client.post("/api/auth/login", data={
            "username": "refreshuser",
            "password": "Test123!"
        })
        refresh_token = login_response.json()["refresh_token"]

        # 刷新 token
        response = await client.post("/api/auth/refresh", json={
            "refresh_token": refresh_token
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_invalid_token(self, client: AsyncClient):
        """无效 refresh token"""
        response = await client.post("/api/auth/refresh", json={
            "refresh_token": "invalid.token.here"
        })
        assert response.status_code == 401
```

### GET /api/auth/me

```python
class TestAuthMe:
    """获取当前用户 API 测试"""

    async def test_get_current_user_success(self, client: AsyncClient, auth_headers):
        """获取当前用户成功"""
        response = await client.get("/api/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "testuser"
        assert data["email"] == "test@example.com"

    async def test_get_current_user_no_token(self, client: AsyncClient):
        """无 token"""
        response = await client.get("/api/auth/me")
        assert response.status_code == 401

    async def test_get_current_user_invalid_token(self, client: AsyncClient):
        """无效 token"""
        response = await client.get("/api/auth/me", headers={
            "Authorization": "Bearer invalid.token"
        })
        assert response.status_code == 401
```

## 项目 API 测试

### GET /api/projects

```python
class TestListProjects:
    """列出项目 API 测试"""

    async def test_list_projects_empty(self, client: AsyncClient, auth_headers):
        """无项目"""
        response = await client.get("/api/projects", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["projects"] == []

    async def test_list_projects_with_data(self, client: AsyncClient, auth_headers):
        """有项目"""
        # 创建项目
        await client.post("/api/projects", headers=auth_headers, json={
            "name": "Test Project",
            "description": "A test project"
        })
        response = await client.get("/api/projects", headers=auth_headers)
        assert response.status_code == 200
        projects = response.json()["projects"]
        assert len(projects) == 1
        assert projects[0]["name"] == "Test Project"

    async def test_list_projects_unauthorized(self, client: AsyncClient):
        """未授权"""
        response = await client.get("/api/projects")
        assert response.status_code == 401
```

### POST /api/projects

```python
class TestCreateProject:
    """创建项目 API 测试"""

    async def test_create_project_success(self, client: AsyncClient, auth_headers):
        """创建成功"""
        response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "New Project",
            "description": "Project description"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Project"
        assert data["description"] == "Project description"
        assert data["status"] == "draft"
        assert "id" in data

    async def test_create_project_without_description(self, client: AsyncClient, auth_headers):
        """无描述创建"""
        response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "Project No Desc"
        })
        assert response.status_code == 201

    async def test_create_project_missing_name(self, client: AsyncClient, auth_headers):
        """缺少名称"""
        response = await client.post("/api/projects", headers=auth_headers, json={
            "description": "No name"
        })
        assert response.status_code == 422
```

### DELETE /api/projects/{id}

```python
class TestDeleteProject:
    """删除项目 API 测试"""

    async def test_delete_project_success(self, client: AsyncClient, auth_headers):
        """删除成功"""
        # 创建项目
        create_response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "To Delete"
        })
        project_id = create_response.json()["id"]

        # 删除
        response = await client.delete(f"/api/projects/{project_id}", headers=auth_headers)
        assert response.status_code == 204

        # 验证已删除
        get_response = await client.get(f"/api/projects/{project_id}", headers=auth_headers)
        assert get_response.status_code == 404

    async def test_delete_project_not_found(self, client: AsyncClient, auth_headers):
        """项目不存在"""
        response = await client.delete(
            "/api/projects/nonexistent-id",
            headers=auth_headers
        )
        assert response.status_code == 404

    async def test_delete_project_other_user(self, client: AsyncClient, auth_headers):
        """删除其他用户的项目"""
        # 用户 B 创建项目
        await client.post("/api/auth/register", json={
            "username": "userb",
            "email": "userb@example.com",
            "password": "Test123!"
        })
        login_response = await client.post("/api/auth/login", data={
            "username": "userb",
            "password": "Test123!"
        })
        userb_token = login_response.json()["access_token"]

        create_response = await client.post("/api/projects", headers={
            "Authorization": f"Bearer {userb_token}"
        }, json={"name": "UserB Project"})
        project_id = create_response.json()["id"]

        # 用户 A 尝试删除
        response = await client.delete(f"/api/projects/{project_id}", headers=auth_headers)
        assert response.status_code == 404
```

## 文档 API 测试

### POST /api/projects/{id}/documents

```python
import io

class TestUploadDocument:
    """上传文档 API 测试"""

    async def test_upload_pdf_success(self, client: AsyncClient, auth_headers):
        """上传 PDF 成功"""
        # 创建项目
        project_response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "Doc Test Project"
        })
        project_id = project_response.json()["id"]

        # 创建 PDF 内容
        pdf_content = b"%PDF-1.4 fake pdf content"

        response = await client.post(
            f"/api/projects/{project_id}/documents",
            headers={**auth_headers},
            data={"doc_type": "tender"},
            files={"file": ("tender.pdf", io.BytesIO(pdf_content), "application/pdf")}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["original_filename"] == "tender.pdf"
        assert data["doc_type"] == "tender"
        assert data["status"] == "pending"

    async def test_upload_docx_success(self, client: AsyncClient, auth_headers):
        """上传 DOCX 成功"""
        project_response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "Docx Test"
        })
        project_id = project_response.json()["id"]

        docx_content = b"PK fake docx content"  # DOCX is a ZIP format

        response = await client.post(
            f"/api/projects/{project_id}/documents",
            headers={**auth_headers},
            data={"doc_type": "bid"},
            files={"file": ("bid.docx", io.BytesIO(docx_content), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        )
        assert response.status_code == 201

    async def test_upload_invalid_type(self, client: AsyncClient, auth_headers):
        """不支持的文件类型"""
        project_response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "Invalid Type Test"
        })
        project_id = project_response.json()["id"]

        response = await client.post(
            f"/api/projects/{project_id}/documents",
            headers={**auth_headers},
            data={"doc_type": "tender"},
            files={"file": ("data.txt", io.BytesIO(b"text content"), "text/plain")}
        )
        assert response.status_code == 400
        assert "Unsupported file type" in response.json()["detail"]

    async def test_upload_invalid_doc_type(self, client: AsyncClient, auth_headers):
        """无效的 doc_type"""
        project_response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "Invalid DocType Test"
        })
        project_id = project_response.json()["id"]

        response = await client.post(
            f"/api/projects/{project_id}/documents",
            headers={**auth_headers},
            data={"doc_type": "invalid"},
            files={"file": ("doc.pdf", io.BytesIO(b"pdf"), "application/pdf")}
        )
        assert response.status_code == 400
```

### GET /api/documents/{id}/content

```python
class TestGetDocumentContent:
    """获取文档内容 API 测试"""

    async def test_get_content_not_parsed(self, client: AsyncClient, auth_headers):
        """文档未解析"""
        project_response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "Content Test"
        })
        project_id = project_response.json()["id"]

        # 上传文档（不等待解析完成）
        pdf_content = b"%PDF-1.4"
        upload_response = await client.post(
            f"/api/projects/{project_id}/documents",
            headers={**auth_headers},
            data={"doc_type": "tender"},
            files={"file": ("tender.pdf", io.BytesIO(pdf_content), "application/pdf")}
        )
        doc_id = upload_response.json()["id"]

        # 尝试获取内容
        response = await client.get(
            f"/api/documents/{doc_id}/content",
            headers=auth_headers
        )
        assert response.status_code == 400
        assert "not parsed" in response.json()["detail"]
```

## 审查 API 测试

### POST /api/projects/{id}/review

```python
class TestStartReview:
    """启动审查 API 测试"""

    async def test_start_review_no_documents(self, client: AsyncClient, auth_headers):
        """无文档时启动审查"""
        project_response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "Review No Docs"
        })
        project_id = project_response.json()["id"]

        response = await client.post(
            f"/api/projects/{project_id}/review",
            headers=auth_headers
        )
        # 应该有文档验证失败
        assert response.status_code in [400, 500]

    async def test_start_review_already_running(self, client: AsyncClient, auth_headers):
        """已有运行中的任务"""
        project_response = await client.post("/api/projects", headers=auth_headers, json={
            "name": "Review Duplicate"
        })
        project_id = project_response.json()["id"]

        # 创建任务（模拟）
        # 实际测试需要文档已解析
        pass
```

### POST /api/review-tasks/{id}/cancel

```python
class TestCancelReview:
    """取消审查任务 API 测试"""

    async def test_cancel_running_task(self, client: AsyncClient, auth_headers):
        """取消运行中的任务"""
        # 需要先创建一个运行中的任务
        # 由于 Celery 任务异步执行，需要 mock 或实际等待
        pass

    async def test_cancel_completed_task(self, client: AsyncClient, auth_headers):
        """取消已完成的任务"""
        # 已完成的任务不能取消
        pass

    async def test_cancel_nonexistent_task(self, client: AsyncClient, auth_headers):
        """取消不存在的任务"""
        response = await client.post(
            "/api/review-tasks/nonexistent-id/cancel",
            headers=auth_headers
        )
        assert response.status_code == 404
```

### GET /api/review-tasks/{id}/results

```python
class TestGetReviewResults:
    """获取审查结果 API 测试"""

    async def test_get_results_empty(self, client: AsyncClient, auth_headers):
        """无结果"""
        # 创建项目和任务（无结果）
        pass

    async def test_get_results_with_data(self, client: AsyncClient, auth_headers):
        """有结果"""
        # 完整审查流程后获取结果
        pass

    async def test_results_sorted_by_severity(self, client: AsyncClient, auth_headers):
        """结果按严重程度排序"""
        # critical -> major -> minor
        pass
```

## SSE API 测试

```python
class TestSSEEvents:
    """SSE 事件流 API 测试"""

    async def test_sse_connection(self, client: AsyncClient, auth_headers):
        """SSE 连接"""
        # 需要先创建任务
        pass

    async def test_sse_events_format(self, client: AsyncClient, auth_headers):
        """事件格式验证"""
        # 验证 event: step, event: complete, event: error 等格式
        pass

    async def test_sse_authentication(self, client: AsyncClient):
        """SSE 认证"""
        # 无 token 应该被拒绝
        pass
```

## 并发测试

```python
class TestConcurrency:
    """并发测试"""

    async def test_concurrent_project_creation(self, client: AsyncClient, auth_headers):
        """并发创建项目"""
        import asyncio

        async def create_project(i):
            return await client.post("/api/projects", headers=auth_headers, json={
                "name": f"Concurrent Project {i}"
            })

        # 并发创建 10 个项目
        tasks = [create_project(i) for i in range(10)]
        responses = await asyncio.gather(*tasks)

        # 所有请求都应成功
        success_count = sum(1 for r in responses if r.status_code == 201)
        assert success_count == 10

    async def test_concurrent_document_uploads(self, client: AsyncClient, auth_headers):
        """并发上传文档"""
        pass

    async def test_max_concurrent_reviews(self, client: AsyncClient, auth_headers):
        """最大并发审查数"""
        # 应该限制为 4 个并发
        pass
```

## 错误处理测试

```python
class TestErrorHandling:
    """错误处理测试"""

    async def test_database_connection_error(self, client: AsyncClient, auth_headers):
        """数据库连接错误"""
        # 需要模拟数据库故障
        pass

    async def test_redis_connection_error(self, client: AsyncClient, auth_headers):
        """Redis 连接错误"""
        # SSE 和 Celery 可能受影响
        pass

    async def test_invalid_json_body(self, client: AsyncClient, auth_headers):
        """无效 JSON body"""
        response = await client.post(
            "/api/projects",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=b"not valid json"
        )
        assert response.status_code == 422

    async def test_missing_required_fields(self, client: AsyncClient, auth_headers):
        """缺少必需字段"""
        response = await client.post(
            "/api/projects",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=b"{}"
        )
        assert response.status_code == 422

    async def test_invalid_uuid_format(self, client: AsyncClient, auth_headers):
        """无效 UUID 格式"""
        response = await client.get(
            "/api/projects/invalid-uuid",
            headers=auth_headers
        )
        assert response.status_code == 422
```
