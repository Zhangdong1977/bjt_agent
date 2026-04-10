from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Body
from typing import List, Annotated
import asyncio
import os
import shutil
import json
from datetime import datetime
from uuid import uuid4

import httpx
from jose import jwt as jose_jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from .deps import get_current_user, get_db_session
from backend.config import get_settings
from backend.models import User
from backend.schemas.knowledge import KnowledgeDocumentResponse, KnowledgeDocumentListResponse
from backend.schemas.auth import TokenData

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

settings = get_settings()


def get_original_filename(user_dir: str, file_id: str) -> str:
    """从元数据文件获取原始文件名，如果不存在则返回file_id"""
    meta_path = os.path.join(user_dir, f"{file_id}.meta.json")
    try:
        if os.path.exists(meta_path):
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
                return meta.get('originalFilename', file_id)
    except (json.JSONDecodeError, IOError):
        pass
    return file_id


@router.get("/documents", response_model=KnowledgeDocumentListResponse)
async def list_documents(current_user: User = Depends(get_current_user)):
    """获取当前用户的所有知识库文档"""
    user_dir = os.path.join(settings.knowledge_base_path, current_user.id)
    if not os.path.exists(user_dir):
        return KnowledgeDocumentListResponse(documents=[], total=0)

    documents = []
    for item in os.listdir(user_dir):
        # 跳过元数据文件和.md转换文件
        if item.endswith('.meta.json') or item.endswith('.md'):
            continue
        item_path = os.path.join(user_dir, item)
        if os.path.isfile(item_path):
            stat = os.stat(item_path)
            original_filename = get_original_filename(user_dir, item)
            documents.append(KnowledgeDocumentResponse(
                id=item,
                filename=original_filename,
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

    # 保存元数据文件（包含原始文件名）
    meta_path = os.path.join(user_dir, f"{unique_filename}.meta.json")
    meta_data = {"originalFilename": file.filename}
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta_data, f, ensure_ascii=False)

    # 触发知识库同步（异步，不阻塞响应）
    asyncio.create_task(sync_knowledge_base(current_user.id))

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

    # 删除元数据文件（如果存在）
    meta_file_path = file_path + ".meta.json"
    if os.path.exists(meta_file_path):
        os.remove(meta_file_path)

    # 触发 RAG 同步（异步，不阻塞响应）
    asyncio.create_task(sync_knowledge_base(current_user.id))

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

    return {"content": content, "filename": get_original_filename(user_dir, document_id)}


def chunk_markdown_content(content: str, tokens: int = 400, overlap: int = 80) -> list:
    """
    将 Markdown 内容分片，与 rag_memory_service 的 chunkMarkdown 逻辑一致。

    Args:
        content: Markdown 文本内容
        tokens: 目标分片大小（token数），默认 400
        overlap: 分片重叠大小（token数），默认 80

    Returns:
        分片列表，每个分片包含 startLine, endLine, text, hash
    """
    max_chars = max(32, tokens * 4)  # tokens * 4，与 rag-memory 一致
    overlap_chars = max(0, overlap * 4)

    lines = content.split('\n')
    chunks = []

    current_chunk_lines = []
    current_length = 0
    start_line = 1

    for i, line in enumerate(lines):
        line_length = len(line) + 1  # +1 for newline

        if current_length + line_length > max_chars and len(current_chunk_lines) > 0:
            # 保存当前分片
            chunk_text = '\n'.join(current_chunk_lines)
            chunks.append({
                "startLine": start_line,
                "endLine": i,
                "text": chunk_text,
            })

            # 开始新分片，包含重叠部分
            overlap_lines = max(1, overlap_chars // 80)  # 估算每行约80字符
            current_chunk_lines = current_chunk_lines[-overlap_lines:]
            current_length = sum(len(l) + 1 for l in current_chunk_lines)
            start_line = i - overlap_lines + 1

        current_chunk_lines.append(line)
        current_length += line_length

    # 处理最后一个分片
    if current_chunk_lines:
        chunk_text = '\n'.join(current_chunk_lines)
        chunks.append({
            "startLine": start_line,
            "endLine": len(lines),
            "text": chunk_text,
        })

    return chunks


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

    # 使用与 rag_memory_service 一致的分片逻辑（字符级）
    chunks = chunk_markdown_content(content)

    # 格式化为与前端期望的格式一致
    shards = []
    for i, chunk in enumerate(chunks):
        shards.append({
            "id": f"shard-{i + 1}",
            "startLine": chunk["startLine"],
            "endLine": chunk["endLine"],
            "content": chunk["text"]
        })

    return {
        "docId": document_id,
        "filename": get_original_filename(user_dir, document_id),
        "shards": shards,
        "totalShards": len(shards)
    }


@router.post("/rag-search")
async def rag_search(
    query: str = Body(..., embed=True),
    limit: int = 10,
    current_user: User = Depends(get_current_user)
):
    """测试知识库RAG搜索"""
    rag_settings = get_settings()

    headers = {"X-User-ID": current_user.id}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{rag_settings.rag_memory_service_url}/api/search",
                json={"query": query, "limit": limit},
                headers=headers
            )
            return response.json()
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="RAG service unavailable")


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

            # 格式化结果，添加文档ID和原始文件名
            formatted_results = []
            user_dir = os.path.join(settings.knowledge_base_path, current_user.id)
            for r in results.get("results", []):
                # 从path中提取文档ID（path格式: /path/to/doc.docx.md）
                path = r.get("path", "")
                doc_id = os.path.basename(path).replace(".md", "")
                original_filename = get_original_filename(user_dir, doc_id)
                formatted_results.append({
                    "source": original_filename,
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
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="RAG service unavailable")


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
            result = response.json()
            # 如果没有文件（files=0 且 chunks=0），认为索引已就绪
            # 因为没有什么需要索引的，避免一直显示"正在索引"
            if result.get("files", 0) == 0 and result.get("chunks", 0) == 0:
                result["status"] = "ready"
            return result
    except httpx.RequestError:
        return {
            "status": "unavailable",
            "files": 0,
            "chunks": 0,
            "provider": "unknown",
            "model": "unknown",
            "lastSync": None
        }


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