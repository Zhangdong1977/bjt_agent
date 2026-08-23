"""Documents API routes."""

import errno
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, status, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.api.deps import DBSession, CurrentUser, is_interior_user
from backend.config import get_settings
from backend.middleware.upload_throttle import throttled_save
from backend.models import Document, Project
from backend.schemas.document import (
    DocumentArtifactsResponse,
    DuplicateBatchAttachRequest,
    DocumentContentResponse,
    DocumentListResponse,
    DocumentResponse,
    DuplicatePairAttachRequest,
)
from backend.services.document_artifacts import (
    load_artifact_manifest,
    load_evidence_blocks,
)

settings = get_settings()
router = APIRouter(prefix="/projects/{project_id}/documents", tags=["Documents"])

# 草稿文档 router：文档可在不关联项目时上传/解析（用户在检查页选文件即上传）
drafts_router = APIRouter(prefix="/documents", tags=["Documents"])

DOCUMENT_NOT_FOUND = "文档不存在或已被删除"
REVIEW_DOC_TYPES = {"tender", "bid"}
DUPLICATE_BID_TYPES = {"duplicate_left", "duplicate_right", "duplicate_bid"}
DUPLICATE_SOURCE_TYPES = {"duplicate_tender", "duplicate_public_reference"}
DUPLICATE_DOC_TYPES = DUPLICATE_BID_TYPES | DUPLICATE_SOURCE_TYPES
ALL_DOC_TYPES = REVIEW_DOC_TYPES | DUPLICATE_DOC_TYPES
# Production Linux/NFS limits each UTF-8 path component to 255 bytes.
MAX_STORAGE_FILENAME_BYTES = 255
INVALID_FILENAME_CHARS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
FILENAME_TOO_LONG_DETAIL = "文件名过长，请缩短文件名后重新上传"
FILENAME_INVALID_DETAIL = "文件名包含系统不支持的特殊字符，请修改文件名后重新上传"


def _validate_upload_filename(filename: str | None) -> str:
    """Reject names that cannot be stored reliably across supported filesystems."""
    if not filename or filename in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=FILENAME_INVALID_DETAIL,
        )

    if any(
        char in INVALID_FILENAME_CHARS or ord(char) < 32 or ord(char) == 127
        for char in filename
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=FILENAME_INVALID_DETAIL,
        )

    stem = Path(filename).stem
    if (
        stem.upper() in WINDOWS_RESERVED_FILENAMES
        or filename.endswith((" ", "."))
        or stem.endswith((" ", "."))
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=FILENAME_INVALID_DETAIL,
        )
    return filename


def _build_storage_filename(filename: str | None, *, timestamp: str | None = None) -> str:
    """Build a readable, filesystem-safe storage name from the original name."""
    filename = _validate_upload_filename(filename)

    source = Path(filename)
    storage_name = (
        f"{source.stem}_{timestamp or datetime.now().strftime('%Y%m%d%H%M%S')}"
        f"{source.suffix}"
    )
    try:
        storage_name_bytes = len(storage_name.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=FILENAME_INVALID_DETAIL,
        ) from exc

    if storage_name_bytes > MAX_STORAGE_FILENAME_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=FILENAME_TOO_LONG_DETAIL,
        )
    return storage_name


def _document_artifacts_response(
    document: Document,
    *,
    include_blocks: bool,
    limit: int,
) -> DocumentArtifactsResponse:
    """Load the persisted S2-0 manifest without exposing filesystem paths."""

    manifest = load_artifact_manifest(document.artifact_manifest_path)
    if manifest is None:
        # Legacy parsed documents may predate S2-0.  Return the persisted
        # coverage summary when available instead of pretending coverage is
        # complete or returning a 500 from a diagnostic endpoint.
        coverage = None
        if document.coverage_summary:
            try:
                from backend.schemas.document_artifacts import CoverageSummary

                coverage = CoverageSummary.model_validate(document.coverage_summary)
            except Exception:
                coverage = None
        return DocumentArtifactsResponse(
            document_id=document.id,
            manifest=None,
            coverage=coverage,
            blocks=[],
            block_count=0,
            truncated=False,
        )

    block_count = manifest.evidence_block_count
    blocks = (
        load_evidence_blocks(document.evidence_blocks_path, limit=limit)
        if include_blocks
        else []
    )
    return DocumentArtifactsResponse(
        document_id=document.id,
        manifest=manifest,
        coverage=manifest.coverage,
        blocks=blocks,
        block_count=block_count,
        truncated=include_blocks and len(blocks) < block_count,
    )


def _allowed_doc_types(project_type: str, duplicate_mode: str = "pair") -> set[str]:
    if project_type != "duplicate":
        return REVIEW_DOC_TYPES
    if duplicate_mode == "batch":
        return {"duplicate_bid", *DUPLICATE_SOURCE_TYPES}
    return {"duplicate_left", "duplicate_right", *DUPLICATE_SOURCE_TYPES}


def _document_role_limit(project_type: str, doc_type: str) -> int:
    """Return a fail-closed per-project/draft limit for one document role."""

    if project_type != "duplicate":
        return 10
    if doc_type in {"duplicate_left", "duplicate_right"}:
        return 1
    if doc_type == "duplicate_bid":
        return 10
    if doc_type == "duplicate_tender":
        return 3
    if doc_type == "duplicate_public_reference":
        return 10
    return 0


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

    if doc_type not in _allowed_doc_types(project.project_type, project.duplicate_mode):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档类型与项目类型不匹配",
        )

    # Check the per-role document limit directly to avoid relationship lazy loading.
    count_result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.doc_type == doc_type,
        )
    )
    existing_count = len(count_result.scalars().all())
    max_count = _document_role_limit(project.project_type, doc_type)
    if existing_count >= max_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该类型文档已达上限（{max_count}个），请先删除后再上传",
        )

    _validate_upload_file(file)

    # Create project directory
    project_dir = settings.workspace_path / str(current_user.id) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # Determine subdirectory based on doc_type
    subdir = doc_type
    doc_dir = project_dir / subdir
    doc_dir.mkdir(exist_ok=True)

    file_path = Path(await _save_upload_file(file, doc_dir, fsync=False))

    # Create document record
    document = Document(
        project_id=project_id,
        doc_type=doc_type,
        original_filename=file.filename,
        file_path=str(file_path),
        status="pending",
    )
    db.add(document)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        file_path.unlink(missing_ok=True)
        if project.project_type == "duplicate" and doc_type in {"duplicate_left", "duplicate_right"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="查重项目每侧仅允许上传一份文件",
            ) from exc
        raise
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

    if file_ext in [".docx", ".doc", ".pdf", ".xlsx"]:
        # Return Markdown content for DOCX/PDF/XLSX files
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


@router.get("/{document_id}/download")
async def download_document(
    project_id: str,
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> FileResponse:
    """Download the original uploaded file of a document.

    Interior users (experience dashboard) may download any user's
    document to retrieve the source attachments of a review task; write
    operations remain restricted to the owner via ``verify_project_ownership``.
    """
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

    file_path = Path(document.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="附件原件不存在或已被清理",
        )

    # media_type 用空串让 FileResponse 走 filename 后缀推断；避免对 doc/docx
    # 等类型硬编码错误的 MIME 导致浏览器拒绝下载。
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=document.original_filename,
    )


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

SUPPORTED_EXTENSIONS = {"pdf", "docx", "doc", "xlsx"}


def _validate_upload_file(file: UploadFile) -> None:
    """上传文件的通用校验：先大小，再校验文件名和扩展名。"""
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

    _validate_upload_filename(file.filename)
    file_ext = Path(file.filename).suffix.lower().lstrip(".")
    if file_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"暂不支持 {file_ext or '未知'} 格式，请上传 PDF、DOCX、DOC 或 XLSX 文件",
        )


async def _save_upload_file(
    file: UploadFile,
    doc_dir: Path,
    *,
    fsync: bool = True,
) -> str:
    """把上传文件保存到指定目录，返回绝对路径。

    写完后 fsync 强制把数据刷到磁盘（NFS 场景下确保写已传播到 server，
    对其它 client 可见），避免「上传后 parser 跨节点读不到文件」的竞态。
    通过 throttled_save 实现单连接上传限速（见 settings.upload_bytes_per_sec）。
    """
    doc_dir.mkdir(parents=True, exist_ok=True)
    unique_filename = _build_storage_filename(file.filename)
    file_path = doc_dir / unique_filename
    try:
        await throttled_save(
            file,
            file_path,
            bytes_per_sec=settings.upload_bytes_per_sec,
            fsync=fsync,
        )
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) == 206:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=FILENAME_TOO_LONG_DETAIL,
            ) from exc
        if getattr(exc, "winerror", None) == 123:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=FILENAME_INVALID_DETAIL,
            ) from exc
        raise
    return str(file_path)


@drafts_router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_draft_document(
    db: DBSession,
    doc_type: str = Query(
        ...,
        description=(
            "文档类型：tender、bid、duplicate_left、duplicate_right、"
            "duplicate_bid、duplicate_tender 或 duplicate_public_reference"
        ),
    ),
    file: UploadFile = File(...),
    current_user: CurrentUser = None,
) -> Document:
    """上传草稿文档（不关联项目），上传后自动开始解析。

    用户在标书检查页选文件时立即调用此接口；点「开始检查」创建项目后，
    再通过 /documents/{doc_id}/attach 把草稿文档关联到项目。
    """
    if doc_type not in ALL_DOC_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档类型不正确",
        )

    if doc_type in {"duplicate_left", "duplicate_right"}:
        existing_result = await db.execute(
            select(Document).where(
                Document.owner_user_id == current_user.id,
                Document.project_id.is_(None),
                Document.doc_type == doc_type,
            )
        )
        if existing_result.scalars().first() is not None:
            side = "A方" if doc_type == "duplicate_left" else "B方"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{side}仅允许上传一份文件，请先删除原文件",
            )

    if doc_type in {"duplicate_bid", "duplicate_tender", "duplicate_public_reference"}:
        existing_result = await db.execute(
            select(Document).where(
                Document.owner_user_id == current_user.id,
                Document.project_id.is_(None),
                Document.doc_type == doc_type,
            )
        )
        if len(existing_result.scalars().all()) >= _document_role_limit("duplicate", doc_type):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该查重文档类型已达到草稿上限，请先删除后再上传",
            )

    _validate_upload_file(file)

    # 草稿文档落盘到 workspace/{user_id}/_drafts/{doc_type}/
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
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        Path(file_path).unlink(missing_ok=True)
        if doc_type in {"duplicate_left", "duplicate_right"}:
            side = "A方" if doc_type == "duplicate_left" else "B方"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{side}仅允许上传一份文件，请先删除原文件",
            ) from exc
        raise
    await db.refresh(document)
    await db.commit()

    from backend.tasks.document_parser import parse_document
    parse_document.delay(document.id)

    return document


@drafts_router.get("/drafts", response_model=DocumentListResponse)
async def list_draft_documents(
    db: DBSession,
    current_user: CurrentUser,
) -> DocumentListResponse:
    """列出当前用户的所有草稿文档（project_id IS NULL）。"""
    result = await db.execute(
        select(Document)
        .where(Document.owner_user_id == current_user.id, Document.project_id.is_(None))
        .order_by(Document.created_at.desc())
    )
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

    allowed_types = _allowed_doc_types(project.project_type, project.duplicate_mode)

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
    if document.doc_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档类型与项目类型不匹配",
        )
    if project.project_type == "duplicate":
        existing_result = await db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type == document.doc_type,
            )
        )
        existing_count = len(existing_result.scalars().all())
        role_limit = _document_role_limit(project.project_type, document.doc_type)
        if existing_count >= role_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"该查重文档角色已达上限（{role_limit} 份）",
            )

    document.project_id = project_id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="查重文档关联冲突，请刷新后重试",
        ) from exc
    await db.refresh(document)
    return document


@router.get("/{document_id}/artifacts", response_model=DocumentArtifactsResponse)
async def get_document_artifacts(
    project_id: str,
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
    include_blocks: bool = Query(False, description="是否返回证据块明细"),
    limit: int = Query(200, ge=1, le=2000, description="证据块返回上限"),
) -> DocumentArtifactsResponse:
    """Return parser coverage and deterministic evidence artifacts.

    This endpoint is readable by the document owner and internal users.  The
    response contains hashes and filenames, never absolute workspace paths.
    """

    await verify_project_ownership(project_id, current_user, db, allow_interior=True)
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.project_id == project_id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DOCUMENT_NOT_FOUND)
    return _document_artifacts_response(document, include_blocks=include_blocks, limit=limit)


@router.post("/attach-duplicate-pair", response_model=list[DocumentResponse])
async def attach_duplicate_pair(
    payload: DuplicatePairAttachRequest,
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[Document]:
    """Atomically attach one parsed A-side and one parsed B-side draft."""
    project = await verify_project_ownership(project_id, current_user, db)
    if project.project_type != "duplicate":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅查重项目可以关联 A/B 文档对",
        )
    if payload.left_document_id == payload.right_document_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A 方和 B 方必须是两份不同文档",
        )

    requested_ids = {
        payload.left_document_id,
        payload.right_document_id,
        *payload.source_document_ids,
    }
    if len(requested_ids) != 2 + len(payload.source_document_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="关联文档列表中存在重复文档",
        )
    rows = await db.execute(
        select(Document).where(Document.id.in_(requested_ids)).with_for_update()
    )
    documents = list(rows.scalars().all())
    by_id = {document.id: document for document in documents}
    left = by_id.get(payload.left_document_id)
    right = by_id.get(payload.right_document_id)
    expected = ((left, "duplicate_left", "A方"), (right, "duplicate_right", "B方"))
    for document, expected_type, side_name in expected:
        if (
            document is None
            or document.owner_user_id != current_user.id
            or document.project_id is not None
            or document.doc_type != expected_type
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{side_name}文档不可关联或文档角色不正确",
            )
        if document.status != "parsed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{side_name}文档尚未解析完成",
            )

    sources = [by_id.get(document_id) for document_id in payload.source_document_ids]
    source_counts = {"duplicate_tender": 0, "duplicate_public_reference": 0}
    for document in sources:
        if (
            document is None
            or document.owner_user_id != current_user.id
            or document.project_id is not None
            or document.doc_type not in DUPLICATE_SOURCE_TYPES
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="来源文档不可关联或文档角色不正确",
            )
        if document.status != "parsed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"来源文档 {document.original_filename} 尚未解析完成",
            )
        source_counts[document.doc_type] += 1
        if source_counts[document.doc_type] > _document_role_limit(
            project.project_type, document.doc_type
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="来源文档数量超过上限",
            )
    existing = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.doc_type.in_({"duplicate_left", "duplicate_right"}),
        )
    )
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="查重项目已经关联 A/B 文档",
        )

    left.project_id = project_id
    right.project_id = project_id
    for source in sources:
        source.project_id = project_id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="查重项目每侧仅允许关联一份文件",
        ) from exc
    await db.refresh(left)
    await db.refresh(right)
    for source in sources:
        await db.refresh(source)
    return [left, right, *sources]


@router.post("/attach-duplicate-batch", response_model=list[DocumentResponse])
async def attach_duplicate_batch(
    payload: DuplicateBatchAttachRequest,
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[Document]:
    """Atomically attach 3–10 parsed duplicate_bid drafts and optional sources."""

    project = await verify_project_ownership(project_id, current_user, db)
    if not settings.duplicate_batch_enabled:
        raise HTTPException(status_code=403, detail="batch duplicate mode is disabled")
    if project.project_type != "duplicate" or project.duplicate_mode != "batch":
        raise HTTPException(status_code=400, detail="当前项目不是批量查重模式")
    member_ids = [item.document_id for item in payload.members]
    if len(set(member_ids)) != len(member_ids):
        raise HTTPException(status_code=400, detail="批量文档列表中存在重复文档")
    source_ids = list(payload.source_document_ids)
    if len(set(source_ids)) != len(source_ids):
        raise HTTPException(status_code=400, detail="批量来源文档列表中存在重复文档")
    if set(member_ids) & set(source_ids):
        raise HTTPException(status_code=400, detail="投标文档和来源文档不能重复")
    requested_ids = set(member_ids) | set(source_ids)
    rows = await db.execute(
        select(Document).where(Document.id.in_(requested_ids)).with_for_update()
    )
    by_id = {document.id: document for document in rows.scalars().all()}
    members: list[Document] = []
    ordinals: set[int] = set()
    party_keys: set[str] = set()
    for index, item in enumerate(payload.members):
        document = by_id.get(item.document_id)
        ordinal = item.ordinal if item.ordinal is not None else index
        if ordinal in ordinals:
            raise HTTPException(status_code=400, detail="批量文档顺序不能重复")
        ordinals.add(ordinal)
        if (
            document is None
            or document.owner_user_id != current_user.id
            or document.project_id is not None
            or document.doc_type != "duplicate_bid"
            or document.status not in {"parsed", "failed"}
        ):
            raise HTTPException(status_code=400, detail="批量投标文档不可关联或尚未解析完成")
        party_key = (item.party_key or f"party-{ordinal + 1}").strip()
        if not party_key or party_key in party_keys:
            raise HTTPException(status_code=400, detail="批量文档投标人标签不能为空或重复")
        party_keys.add(party_key)
        document.duplicate_party_key = party_key
        document.duplicate_display_name = (
            item.display_name or document.original_filename
        ).strip()
        document.duplicate_ordinal = ordinal
        members.append(document)

    sources: list[Document] = []
    source_counts = {"duplicate_tender": 0, "duplicate_public_reference": 0}
    for source_id in source_ids:
        document = by_id.get(source_id)
        if (
            document is None
            or document.owner_user_id != current_user.id
            or document.project_id is not None
            or document.doc_type not in DUPLICATE_SOURCE_TYPES
            or document.status != "parsed"
        ):
            raise HTTPException(status_code=400, detail="批量来源文档不可关联或尚未解析完成")
        source_counts[document.doc_type] += 1
        if source_counts[document.doc_type] > _document_role_limit(
            project.project_type, document.doc_type
        ):
            raise HTTPException(status_code=400, detail="批量来源文档数量超过上限")
        sources.append(document)

    existing = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.doc_type.in_({"duplicate_bid", "duplicate_left", "duplicate_right"}),
        )
    )
    if existing.scalars().first() is not None:
        raise HTTPException(status_code=400, detail="查重项目已经关联投标文档")

    for document in [*members, *sources]:
        document.project_id = project_id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="批量文档关联冲突，请刷新后重试") from exc
    for document in [*members, *sources]:
        await db.refresh(document)
    return [*members, *sources]


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

    if file_ext in [".docx", ".doc", ".pdf", ".xlsx"]:
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


@drafts_router.get("/{document_id}/artifacts", response_model=DocumentArtifactsResponse)
async def get_draft_document_artifacts(
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
    include_blocks: bool = Query(False, description="是否返回证据块明细"),
    limit: int = Query(200, ge=1, le=2000, description="证据块返回上限"),
) -> DocumentArtifactsResponse:
    """Return S2-0 artifacts for an owned, project-less draft document."""

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.owner_user_id == current_user.id,
            Document.project_id.is_(None),
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DOCUMENT_NOT_FOUND)
    return _document_artifacts_response(document, include_blocks=include_blocks, limit=limit)
