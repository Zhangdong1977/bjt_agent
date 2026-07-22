"""Documents API routes."""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, status, Query
from sqlalchemy import func, select

from backend.api.deps import DBSession, CurrentUser, is_interior_user
from backend.config import get_settings
from backend.middleware.upload_throttle import throttled_save
from backend.models import Document, Project
from backend.schemas.document import DocumentResponse, DocumentListResponse, DocumentContentResponse

settings = get_settings()
router = APIRouter(prefix="/projects/{project_id}/documents", tags=["Documents"])

# 草稿文档 router：文档可在不关联项目时上传/解析（用户在检查页选文件即上传）
drafts_router = APIRouter(prefix="/documents", tags=["Documents"])

DOCUMENT_NOT_FOUND = "文档不存在或已被删除"


async def verify_project_ownership(
    project_id: str,
    current_user,
    db: DBSession,
    *,
    allow_interior: bool = False,
) -> Project:
    """Verify that the project exists and the caller may access it.

    Regular users may only access their own projects. When ``allow_interior``
    is set, internal users (see :func:`is_interior_user`) may access any
    project — used by read endpoints surfaced on the experience dashboard.
    Write operations (upload/delete) must keep ``allow_interior=False`` so
    internal users cannot mutate others' data.
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )
    if allow_interior and is_interior_user(current_user):
        return project
    if project.user_id != current_user.id or project.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )
    return project


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> DocumentListResponse:
    """List all documents in a project."""
    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.created_at.desc())
    )
    documents = result.scalars().all()
    return DocumentListResponse(documents=documents)


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    project_id: str,
    db: DBSession,
    doc_type: str = Query(..., description="文档类型：tender（招标文件）或 bid（投标文件）"),
    file: UploadFile = File(...),
    current_user: CurrentUser = None,
) -> Document:
    """Upload a document to a project.

    After uploading, the document will be automatically parsed
    to extract text content and images.
    """
    project = await verify_project_ownership(project_id, current_user, db)

    if doc_type not in ("tender", "bid", "duplicate_bid"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档类型不正确",
        )

    expected_project_type = "duplicate" if doc_type == "duplicate_bid" else "review"
    if project.project_type != expected_project_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档类型与项目类型不匹配",
        )

    # Check document count limit per type — duplicate check is strictly capped at five.
    count_result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.doc_type == doc_type,
        )
    )
    existing_count = len(count_result.scalars().all())
    max_count = settings.duplicate_check_max_documents if doc_type == "duplicate_bid" else 10
    if existing_count >= max_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该类型文档已达上限（{max_count}个），请先删除后再上传",
        )

    # Validate file extension
    supported_extensions = {"pdf", "docx", "doc"}
    file_ext = Path(file.filename).suffix.lower().lstrip(".")
    if file_ext not in supported_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"暂不支持 {file_ext or '未知'} 格式，请上传 PDF、DOCX 或 DOC 文件",
        )

    # Validate file size - check content length without reading into memory
    file.file.seek(0, 2)  # Seek to end
    file_size = file.file.tell()
    file.file.seek(0)  # Reset to beginning

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件内容为空，请重新选择文件",
        )

    if file_size > settings.max_upload_size_bytes:
        max_mb = settings.max_upload_size_mb
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件过大（{file_size / (1024*1024):.2f} MB），最大支持 {max_mb} MB",
        )

    # Create project directory
    project_dir = settings.workspace_path / str(current_user.id) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # Determine subdirectory based on doc_type
    subdir = doc_type
    doc_dir = project_dir / subdir
    doc_dir.mkdir(exist_ok=True)

    # Save file with unique name to avoid conflicts
    unique_filename = f"{Path(file.filename).stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{Path(file.filename).suffix}"
    file_path = doc_dir / unique_filename
    await throttled_save(file, file_path, bytes_per_sec=settings.upload_bytes_per_sec)

    # Create document record
    document = Document(
        project_id=project_id,
        doc_type=doc_type,
        original_filename=file.filename,
        file_path=str(file_path),
        status="pending",
    )
    db.add(document)
    await db.flush()
    await db.refresh(document)

    # Commit transaction before triggering async task to ensure document is visible
    await db.commit()

    # Trigger document parsing task
    from backend.tasks.document_parser import parse_document
    parse_document.delay(document.id)

    return document


@router.get("/{document_id}/content")
async def get_document_content(
    project_id: str,
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> DocumentContentResponse:
    """Get the parsed content of a document."""
    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(Document)
        .where(Document.id == document_id, Document.project_id == project_id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DOCUMENT_NOT_FOUND,
        )

    if document.status != "parsed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文档尚未解析完成，当前状态：{document.status}",
        )

    # Determine content format based on file extension
    file_ext = Path(document.file_path).suffix.lower()

    content = ""
    content_format = "html"
    images = []

    if file_ext in [".docx", ".doc", ".pdf"]:
        # Return Markdown content for DOCX/PDF files
        content_format = "markdown"
        workspace_dir = settings.workspace_path
        workspace_rel_path = ""
        if document.parsed_markdown_path and Path(document.parsed_markdown_path).exists():
            content = Path(document.parsed_markdown_path).read_text(encoding="utf-8")
            # Fix relative image paths in markdown to use /files/ URLs
            if document.parsed_images_dir and Path(document.parsed_images_dir).exists():
                workspace_rel_path = Path(document.parsed_images_dir).relative_to(workspace_dir).parent
                import re
                def fix_markdown_img_src(match):
                    alt_text = match.group(1)
                    src = match.group(2)
                    # Skip external URLs
                    if src.startswith(('http://', 'https://')):
                        return match.group(0)
                    # Already rewritten
                    if src.startswith('/files/'):
                        return match.group(0)
                    # Absolute local path within workspace -> rewrite to /files/ URL
                    abs_workspace = str(workspace_dir)
                    if src.startswith(abs_workspace):
                        rel = Path(src).relative_to(workspace_dir)
                        return f"![{alt_text}](/files/{rel})"
                    # Relative path -> prefix with workspace rel path
                    new_src = f"/files/{workspace_rel_path}/{src}"
                    return f"![{alt_text}]({new_src})"
                content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', fix_markdown_img_src, content)
        elif document.parsed_html_path and Path(document.parsed_html_path).exists():
            # Fallback to HTML if markdown is not available (legacy DOCX)
            content_format = "html"
            html_content = Path(document.parsed_html_path).read_text(encoding="utf-8")

            # Fix relative image paths in HTML to use /files/ URLs
            workspace_dir = settings.workspace_path
            if document.parsed_images_dir and Path(document.parsed_images_dir).exists():
                import re

                workspace_rel_path = Path(document.parsed_images_dir).relative_to(workspace_dir).parent

                def fix_img_src(match):
                    img_tag = match.group(0)
                    src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
                    if not src_match:
                        return img_tag
                    src = src_match.group(1)
                    if src.startswith(('http://', 'https://')):
                        return img_tag
                    if src.startswith('/files/'):
                        return img_tag
                    abs_workspace = str(workspace_dir)
                    if src.startswith(abs_workspace):
                        rel = Path(src).relative_to(workspace_dir)
                        new_src = f"/files/{rel}"
                        return img_tag.replace(f'"{src}"', f'"{new_src}"').replace(f"'{src}'", f"'{new_src}'")
                    new_src = f"/files/{workspace_rel_path}/{src}"
                    return img_tag.replace(f'"{src}"', f'"{new_src}"').replace(f"'{src}'", f"'{new_src}'")

                html_content = re.sub(r'<img[^>]+>', fix_img_src, html_content)
            content = html_content

        # Get image paths
        workspace_dir = settings.workspace_path
        if document.parsed_images_dir and Path(document.parsed_images_dir).exists():
            for p in Path(document.parsed_images_dir).iterdir():
                if p.is_file():
                    rel_path = p.relative_to(workspace_dir)
                    images.append(f"/files/{rel_path}")

    return DocumentContentResponse(content=content, images=images, format=content_format)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    project_id: str,
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> Document:
    """Get a document by ID."""
    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(Document)
        .where(Document.id == document_id, Document.project_id == project_id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DOCUMENT_NOT_FOUND,
        )
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    project_id: str,
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> None:
    """Delete a document."""
    await verify_project_ownership(project_id, current_user, db)

    result = await db.execute(
        select(Document)
        .where(Document.id == document_id, Document.project_id == project_id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DOCUMENT_NOT_FOUND,
        )

    # Delete physical file if exists
    file_path = Path(document.file_path)
    if file_path.exists():
        file_path.unlink()

    await db.delete(document)


# ============================================================
# 草稿文档（独立于项目）：选文件即上传解析，点「开始检查」时才关联到项目
# ============================================================

SUPPORTED_EXTENSIONS = {"pdf", "docx", "doc"}


def _validate_upload_file(file: UploadFile) -> None:
    """上传文件的通用校验：扩展名 + 大小。供项目文档上传和草稿上传复用。"""
    file_ext = Path(file.filename).suffix.lower().lstrip(".")
    if file_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"暂不支持 {file_ext or '未知'} 格式，请上传 PDF、DOCX 或 DOC 文件",
        )

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件内容为空，请重新选择文件",
        )
    if file_size > settings.max_upload_size_bytes:
        max_mb = settings.max_upload_size_mb
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件过大（{file_size / (1024*1024):.2f} MB），最大支持 {max_mb} MB",
        )


async def _save_upload_file(file: UploadFile, doc_dir: Path) -> str:
    """把上传文件保存到指定目录，返回绝对路径。

    写完后 fsync 强制把数据刷到磁盘（NFS 场景下确保写已传播到 server，
    对其它 client 可见），避免「上传后 parser 跨节点读不到文件」的竞态。
    通过 throttled_save 实现单连接上传限速（见 settings.upload_bytes_per_sec）。
    """
    doc_dir.mkdir(parents=True, exist_ok=True)
    unique_filename = (
        f"{Path(file.filename).stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        f"{Path(file.filename).suffix}"
    )
    file_path = doc_dir / unique_filename
    await throttled_save(
        file, file_path, bytes_per_sec=settings.upload_bytes_per_sec, fsync=True
    )
    return str(file_path)


@drafts_router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_draft_document(
    db: DBSession,
    doc_type: str = Query(..., description="文档类型：tender（招标文件）或 bid（投标文件）"),
    file: UploadFile = File(...),
    current_user: CurrentUser = None,
) -> Document:
    """上传草稿文档（不关联项目），上传后自动开始解析。

    用户在标书检查页选文件时立即调用此接口；点「开始检查」创建项目后，
    再通过 /documents/{doc_id}/attach 把草稿文档关联到项目。
    """
    if doc_type not in ("tender", "bid", "duplicate_bid"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档类型不正确",
        )

    _validate_upload_file(file)

    if doc_type == "duplicate_bid":
        count = await db.scalar(
            select(func.count(Document.id)).where(
                Document.owner_user_id == current_user.id,
                Document.project_id.is_(None),
                Document.doc_type == "duplicate_bid",
            )
        )
        if (count or 0) >= settings.duplicate_check_max_documents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"待查重标书最多上传 {settings.duplicate_check_max_documents} 份",
            )

    # 草稿文档落盘到 workspace/{user_id}/_drafts/{tender|bid}/
    draft_dir = settings.workspace_path / str(current_user.id) / "_drafts" / doc_type
    file_path = await _save_upload_file(file, draft_dir)

    document = Document(
        project_id=None,
        owner_user_id=current_user.id,
        doc_type=doc_type,
        original_filename=file.filename,
        file_path=file_path,
        status="pending",
    )
    db.add(document)
    await db.flush()
    await db.refresh(document)
    await db.commit()

    from backend.tasks.document_parser import parse_document
    parse_document.delay(document.id)

    return document


@drafts_router.get("/drafts", response_model=DocumentListResponse)
async def list_draft_documents(
    db: DBSession,
    current_user: CurrentUser,
    doc_type: str | None = Query(None),
) -> DocumentListResponse:
    """列出当前用户的所有草稿文档（project_id IS NULL）。"""
    stmt = select(Document).where(
        Document.owner_user_id == current_user.id, Document.project_id.is_(None)
    )
    if doc_type:
        if doc_type not in ("tender", "bid", "duplicate_bid"):
            raise HTTPException(status_code=400, detail="文档类型不正确")
        stmt = stmt.where(Document.doc_type == doc_type)
    result = await db.execute(stmt.order_by(Document.created_at.desc()))
    documents = result.scalars().all()
    return DocumentListResponse(documents=documents)


@drafts_router.post("/{document_id}/attach", response_model=DocumentResponse)
async def attach_draft_document(
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
    project_id: str = Query(..., description="要关联到的项目 ID"),
) -> Document:
    """把草稿文档关联到项目（点「开始检查」创建项目后调用）。

    仅更新 document.project_id，不移动文件——解析产物用绝对路径存库，
    审查/预览直接读绝对路径，不依赖目录结构。
    """
    # 校验项目归属
    project_result = await db.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if not project or project.user_id != current_user.id or project.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )

    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DOCUMENT_NOT_FOUND,
        )
    # 仅允许归属当前用户的草稿文档被关联
    if document.owner_user_id != current_user.id or document.project_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该文档不可关联（非草稿或不属于当前用户）",
        )

    expected_project_type = "duplicate" if document.doc_type == "duplicate_bid" else "review"
    if project.project_type != expected_project_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档类型与项目类型不匹配",
        )

    if document.doc_type == "duplicate_bid":
        count = await db.scalar(
            select(func.count(Document.id)).where(
                Document.project_id == project_id,
                Document.doc_type == "duplicate_bid",
            )
        )
        if (count or 0) >= settings.duplicate_check_max_documents:
            raise HTTPException(status_code=400, detail="待查重标书已达 5 份上限")

    document.project_id = project_id
    await db.commit()
    await db.refresh(document)
    return document


@drafts_router.get("/{document_id}", response_model=DocumentResponse)
async def get_draft_document(
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> Document:
    document = (await db.execute(select(Document).where(
        Document.id == document_id,
        Document.owner_user_id == current_user.id,
        Document.project_id.is_(None),
    ))).scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail=DOCUMENT_NOT_FOUND)
    return document


@drafts_router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft_document(
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> None:
    """删除草稿文档（仅限 project_id IS NULL 的草稿）。"""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DOCUMENT_NOT_FOUND,
        )
    if document.owner_user_id != current_user.id or document.project_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该文档不可删除（非草稿或不属于当前用户）",
        )

    file_path = Path(document.file_path)
    if file_path.exists():
        file_path.unlink()

    await db.delete(document)


@drafts_router.get("/{document_id}/content")
async def get_draft_document_content(
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> DocumentContentResponse:
    """获取草稿文档的解析内容（用于解析完成后预览）。"""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DOCUMENT_NOT_FOUND,
        )
    if document.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该文档",
        )
    if document.status != "parsed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文档尚未解析完成，当前状态：{document.status}",
        )

    # 复用项目文档的内容读取逻辑（基于绝对路径，与 project_id 无关）
    file_ext = Path(document.file_path).suffix.lower()
    content = ""
    content_format = "html"
    images = []

    if file_ext in [".docx", ".doc", ".pdf"]:
        content_format = "markdown"
        workspace_dir = settings.workspace_path
        workspace_rel_path = ""
        if document.parsed_markdown_path and Path(document.parsed_markdown_path).exists():
            content = Path(document.parsed_markdown_path).read_text(encoding="utf-8")
            if document.parsed_images_dir and Path(document.parsed_images_dir).exists():
                workspace_rel_path = Path(document.parsed_images_dir).relative_to(workspace_dir).parent
                import re

                def fix_markdown_img_src(match):
                    alt_text = match.group(1)
                    src = match.group(2)
                    if src.startswith(("http://", "https://")):
                        return match.group(0)
                    if src.startswith("/files/"):
                        return match.group(0)
                    abs_workspace = str(workspace_dir)
                    if src.startswith(abs_workspace):
                        rel = Path(src).relative_to(workspace_dir)
                        return f"![{alt_text}](/files/{rel})"
                    new_src = f"/files/{workspace_rel_path}/{src}"
                    return f"![{alt_text}]({new_src})"

                content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', fix_markdown_img_src, content)
        elif document.parsed_html_path and Path(document.parsed_html_path).exists():
            content_format = "html"
            content = Path(document.parsed_html_path).read_text(encoding="utf-8")

        if document.parsed_images_dir and Path(document.parsed_images_dir).exists():
            for p in Path(document.parsed_images_dir).iterdir():
                if p.is_file():
                    rel_path = p.relative_to(workspace_dir)
                    images.append(f"/files/{rel_path}")

    return DocumentContentResponse(content=content, images=images, format=content_format)
