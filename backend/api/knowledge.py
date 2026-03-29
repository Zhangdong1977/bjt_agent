from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Body
from typing import List, Annotated
import asyncio
import os
import shutil
from datetime import datetime
from uuid import uuid4

import httpx
from jose import jwt as jose_jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from .deps import get_current_user, get_db_session
from backend.config import get_settings
from backend.models import User
from schemas.knowledge import KnowledgeDocumentResponse, KnowledgeDocumentListResponse
from schemas.auth import TokenData

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

settings = get_settings()

@router.get("/documents", response_model=KnowledgeDocumentListResponse)
async def list_documents(current_user: User = Depends(get_current_user)):
    """获取当前用户的所有知识库文档"""
    user_dir = os.path.join(settings.knowledge_base_path, current_user.id)
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
    user_dir = os.path.join(settings.knowledge_base_path, current_user.id)
    os.makedirs(user_dir, exist_ok=True)

    # 生成唯一文件名
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = os.path.join(user_dir, unique_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    stat = os.stat(file_path)

    # 触发知识库同步（异步，不阻塞响应）
    asyncio.create_task(sync_knowledge_base())

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
    user_dir = os.path.join(settings.knowledge_base_path, current_user.id)
    file_path = os.path.join(user_dir, document_id)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Document not found")

    # 安全检查：确保文件在用户目录下
    if not file_path.startswith(os.path.abspath(user_dir)):
        raise HTTPException(status_code=403, detail="Access denied")

    # 删除原始文件
    os.remove(file_path)

    # 删除对应的 .md 转换文件（如果存在）
    md_file_path = file_path + ".md"
    if os.path.exists(md_file_path):
        os.remove(md_file_path)

    # 触发 RAG 同步（异步，不阻塞响应）
    asyncio.create_task(sync_knowledge_base())

    return {"message": "Document deleted"}

@router.get("/documents/{document_id}/preview")
async def preview_document(
    document_id: str,
    token: str = None,
    db: AsyncSession = Depends(get_db_session)
):
    """预览知识库文档

    Args:
        document_id: 文档ID
        token: 可选的认证token（用于直接浏览器访问）
        db: 数据库会话
    """
    user = None
    if token:
        # Query param 方式验证 token
        try:
            payload = jose_jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            user_id: str = payload.get("sub")
            if user_id:
                from sqlalchemy import select
                result = await db.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()
        except JWTError:
            pass

    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_dir = os.path.join(settings.knowledge_base_path, user.id)
    file_path = os.path.join(user_dir, document_id)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Document not found")

    # 安全检查：确保文件在用户目录下
    if not file_path.startswith(os.path.abspath(user_dir)):
        raise HTTPException(status_code=403, detail="Access denied")

    # 返回文件内容让浏览器直接显示
    from fastapi.responses import FileResponse
    return FileResponse(file_path)


@router.get("/documents/{document_id}/content")
async def get_document_content(
    document_id: str,
    current_user: User = Depends(get_current_user)
):
    """获取知识库文档的Markdown内容"""
    user_dir = os.path.join(settings.knowledge_base_path, current_user.id)
    file_path = os.path.join(user_dir, f"{document_id}.md")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Document not found")

    # 安全检查：确保文件在用户目录下
    if not file_path.startswith(os.path.abspath(user_dir)):
        raise HTTPException(status_code=403, detail="Access denied")

    # 读取文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    return {"content": content, "filename": document_id}


@router.post("/rag-search")
async def rag_search(
    query: str = Body(..., embed=True),
    limit: int = 10,
    current_user: User = Depends(get_current_user)
):
    """测试知识库RAG搜索"""
    rag_settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{rag_settings.rag_memory_service_url}/api/search",
                json={"query": query, "limit": limit}
            )
            return response.json()
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="RAG service unavailable")


async def sync_knowledge_base() -> bool:
    """Trigger rag_memory_service to sync knowledge base index."""
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.rag_memory_service_url}/api/sync",
                json={"force": False}
            )
            return response.status_code == 200
    except httpx.RequestError:
        return False
    except httpx.HTTPStatusError:
        return False