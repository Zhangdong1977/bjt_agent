"""Documents API routes."""

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, status
from sqlalchemy import select

from backend.api.deps import DBSession, CurrentUser
from backend.config import get_settings
from backend.models import Document, Project
from backend.schemas.document import DocumentResponse, DocumentListResponse, DocumentContentResponse

settings = get_settings()
router = APIRouter(prefix="/projects/{project_id}/documents", tags=["Documents"])

DOCUMENT_NOT_FOUND = "Document not found"


async def verify_project_ownership(project_id: str, user_id: str, db: DBSession) -> Project:
    """Verify that the project exists and belongs to the user."""
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> DocumentListResponse:
    """List all documents in a project."""
    await verify_project_ownership(project_id, current_user.id, db)

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
    doc_type: str,
    db: DBSession,
    file: UploadFile = File(...),
    current_user: CurrentUser = None,
) -> Document:
    """Upload a document to a project.

    After uploading, the document will be automatically parsed
    to extract text content and images.
    """
    await verify_project_ownership(project_id, current_user.id, db)

    if doc_type not in ("tender", "bid"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="doc_type must be 'tender' or 'bid'",
        )

    # Validate file extension
    supported_extensions = {"pdf", "docx", "doc"}
    file_ext = Path(file.filename).suffix.lower().lstrip(".")
    if file_ext not in supported_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {file_ext}. Supported: {', '.join(supported_extensions)}",
        )

    # Validate file size - check content length without reading into memory
    file.file.seek(0, 2)  # Seek to end
    file_size = file.file.tell()
    file.file.seek(0)  # Reset to beginning

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file is not allowed",
        )

    if file_size > settings.max_upload_size_bytes:
        max_mb = settings.max_upload_size_mb
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size ({file_size / (1024*1024):.2f} MB) exceeds maximum allowed size ({max_mb} MB)",
        )

    # Create project directory
    project_dir = settings.workspace_path / str(current_user.id) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # Determine subdirectory based on doc_type
    subdir = "tender" if doc_type == "tender" else "bid"
    doc_dir = project_dir / subdir
    doc_dir.mkdir(exist_ok=True)

    # Save file with unique name to avoid conflicts
    unique_filename = f"{Path(file.filename).stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{Path(file.filename).suffix}"
    file_path = doc_dir / unique_filename
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

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
    await verify_project_ownership(project_id, current_user.id, db)

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
            detail=f"Document is not parsed yet. Current status: {document.status}",
        )

    # Read parsed markdown
    md_content = ""
    if document.parsed_md_path and Path(document.parsed_md_path).exists():
        md_content = Path(document.parsed_md_path).read_text(encoding="utf-8")

    # Get image paths
    images = []
    if document.parsed_images_dir and Path(document.parsed_images_dir).exists():
        images = [str(p) for p in Path(document.parsed_images_dir).iterdir() if p.is_file()]

    return DocumentContentResponse(md_content=md_content, images=images)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    project_id: str,
    document_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> Document:
    """Get a document by ID."""
    await verify_project_ownership(project_id, current_user.id, db)

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
    await verify_project_ownership(project_id, current_user.id, db)

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
