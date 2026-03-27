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