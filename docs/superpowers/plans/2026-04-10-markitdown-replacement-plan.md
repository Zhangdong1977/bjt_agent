# Markitdown 替换 LibreOffice 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 LibreOffice 文档解析替换为 markitdown，DOCX/DOC 解析输出 Markdown，前端使用 marked 渲染

**Architecture:** markitdown 作为 Python 库调用，将 DOCX/DOC 转换为 Markdown。PDF 解析保持不变。前端通过 API 返回 Markdown 内容，使用 marked + DOMPurify 渲染。

**Tech Stack:** markitdown (Microsoft), marked, DOMPurify, FastAPI, Celery

---

## 文件结构概览

```
bjt_agent/
├── third_party/
│   └── markitdown/                    # [新建] git submodule
├── backend/
│   ├── parsers/
│   │   └── markitdown_converter.py   # [新建] MarkitdownConverter 类
│   ├── tasks/
│   │   └── document_parser.py        # [修改] 集成 markitdown
│   ├── api/
│   │   └── documents.py              # [修改] 返回 Markdown 内容
│   ├── models/
│   │   └── document.py               # [修改] 添加 parsed_markdown_path 字段
│   └── schemas/
│       └── document.py               # [修改] DocumentContentResponse
└── frontend/
    └── src/
        └── views/
            └── ProjectView.vue       # [修改] 使用 marked 渲染 Markdown
```

---

## Task 1: 添加 markitdown git submodule

**Files:**
- Create: `third_party/markitdown/` (submodule)

- [ ] **Step 1: 添加 git submodule**

Run:
```bash
cd /home/openclaw/bjt_agent && git submodule add https://github.com/microsoft/markitdown.git third_party/markitdown
```

Expected: Submodule added successfully

- [ ] **Step 2: 验证 submodule**

Run:
```bash
ls -la third_party/markitdown/
git submodule status
```

Expected: 显示 markitdown 目录内容

- [ ] **Step 3: 提交**

```bash
git add third_party/markitdown .gitmodules
git commit -m "feat: add markitdown as git submodule

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: 创建 MarkitdownConverter 类

**Files:**
- Create: `backend/parsers/markitdown_converter.py`

- [ ] **Step 1: 创建 MarkitdownConverter 类**

Create file `backend/parsers/markitdown_converter.py`:

```python
"""Markitdown converter module for DOCX/DOC to Markdown conversion."""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MarkitdownConversionError(Exception):
    """Raised when markitdown conversion fails."""
    pass


@dataclass
class ImageInfo:
    """Image information extracted from document."""
    filename: str
    data: bytes


@dataclass
class ConversionResult:
    """Result of document conversion."""
    markdown_content: str
    images: list[ImageInfo]
    page_count: Optional[int] = None


class MarkitdownConverter:
    """Markitdown converter for DOCX/DOC files to Markdown format.

    Uses the markitdown library to extract text and images from documents.
    """

    def __init__(self, timeout: int = 300):
        """Initialize the converter.

        Args:
            timeout: Maximum time in seconds for conversion (default: 5 minutes)
        """
        self.timeout = timeout

    def convert(self, file_path: Path) -> ConversionResult:
        """Convert a DOCX/DOC file to Markdown format.

        Args:
            file_path: Path to the input DOCX/DOC file

        Returns:
            ConversionResult with markdown_content, images, and page_count

        Raises:
            MarkitdownConversionError: If conversion fails
            FileNotFoundError: If input file doesn't exist
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix not in [".docx", ".doc"]:
            raise ValueError(f"Unsupported file type: {suffix}. Expected .docx or .doc")

        file_size = file_path.stat().st_size
        logger.info(f"Markitdown conversion: {file_path} ({file_size / (1024 * 1024):.2f}MB)")

        try:
            # Add markitdown to path
            import sys
            markitdown_path = Path(__file__).parent.parent.parent / "third_party" / "markitdown"
            if str(markitdown_path) not in sys.path:
                sys.path.insert(0, str(markitdown_path))

            from markitdown import MarkItDown

            markitdown = MarkItDown()
            result = markitdown.convert(str(file_path.resolve()))

            # Extract images from result
            images = self._extract_images(result)

            logger.info(f"Markitdown conversion successful: {len(result.text_content)} characters, {len(images)} images")

            return ConversionResult(
                markdown_content=result.text_content or "",
                images=images,
                page_count=None
            )

        except Exception as e:
            logger.error(f"Markitdown conversion failed: {e}")
            raise MarkitdownConversionError(f"Markitdown conversion failed: {e}")

    def _extract_images(self, result) -> list[ImageInfo]:
        """Extract images from markitdown result.

        Args:
            result: MarkItDown result object

        Returns:
            List of ImageInfo objects
        """
        images = []

        # markitdown stores images in result.images attribute
        # Each image has: path, name, data (bytes)
        if hasattr(result, 'images') and result.images:
            for img in result.images:
                if hasattr(img, 'data') and hasattr(img, 'name'):
                    images.append(ImageInfo(
                        filename=img.name,
                        data=img.data
                    ))
                elif isinstance(img, dict):
                    images.append(ImageInfo(
                        filename=img.get('name', 'image'),
                        data=img.get('data', b'')
                    ))

        return images


# Module-level convenience function
def convert_to_markdown(file_path: Path) -> ConversionResult:
    """Convert a DOCX/DOC file to Markdown format.

    Args:
        file_path: Path to the input DOCX/DOC file

    Returns:
        ConversionResult with markdown_content, images, and page_count

    Raises:
        FileNotFoundError: If input file doesn't exist
        MarkitdownConversionError: If conversion fails
        ValueError: If file type is not supported
    """
    converter = MarkitdownConverter()
    return converter.convert(file_path)
```

- [ ] **Step 2: 提交**

```bash
git add backend/parsers/markitdown_converter.py
git commit -m "feat: add MarkitdownConverter class for DOCX/DOC to Markdown conversion

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: 更新 Document 模型和 Schema

**Files:**
- Modify: `backend/models/document.py:24-25`
- Modify: `backend/schemas/document.py:32-36`

- [ ] **Step 1: 更新 Document 模型**

Modify `backend/models/document.py` - add `parsed_markdown_path` field after `parsed_html_path`:

```python
    parsed_html_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parsed_markdown_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # Markdown 文件路径
    parsed_images_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

- [ ] **Step 2: 更新 DocumentContentResponse Schema**

Modify `backend/schemas/document.py` - add `format` field:

```python
class DocumentContentResponse(BaseModel):
    """Schema for document content response."""

    content: str  # renamed from html_content
    images: list[str]
    format: str  # "markdown" or "html"
```

- [ ] **Step 3: 提交**

```bash
git add backend/models/document.py backend/schemas/document.py
git commit -m "feat: add parsed_markdown_path field to Document model

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: 修改文档解析任务集成 markitdown

**Files:**
- Modify: `backend/tasks/document_parser.py:244-253` (body of _parse_docx)
- Modify: `backend/tasks/document_parser.py:173-220` (_save_parsed_content)

- [ ] **Step 1: 修改 _parse_docx 函数使用 markitdown**

Replace the `_parse_docx` function body (lines 401-473) to use MarkitdownConverter:

```python
async def _parse_docx(file_path: Path) -> dict:
    """Parse DOCX file using markitdown.

    Args:
        file_path: Path to the DOCX file

    Returns:
        Dict with text (Markdown), images, and page_count (None)
    """
    from backend.parsers.markitdown_converter import MarkitdownConverter

    file_size = file_path.stat().st_size
    logger.info(f"Markitdown parsing: {file_path} ({file_size / (1024*1024):.2f}MB)")

    converter = MarkitdownConverter()
    result = converter.convert(file_path)

    markdown_content = result.markdown_content
    logger.info(f"Markitdown conversion successful: {len(markdown_content)} characters")

    # Determine the target images directory in workspace
    workspace_images_dir = file_path.parent / f"{file_path.stem}_images"

    # Save images to workspace
    images = []
    if result.images:
        workspace_images_dir.mkdir(parents=True, exist_ok=True)
        for img_info in result.images:
            img_path = workspace_images_dir / img_info.filename
            img_path.write_bytes(img_info.data)
            images.append({
                "filename": img_info.filename,
                "data": img_info.data,
            })
        logger.info(f"Saved {len(images)} images to {workspace_images_dir}")

    logger.info(f"Extracted {len(images)} images from DOCX")

    return {
        "text": markdown_content,  # Markdown content
        "images": images,
        "page_count": None,
    }
```

- [ ] **Step 2: 修改 _save_parsed_content 保存 Markdown 文件**

Update the `_save_parsed_content` function to:
1. Save Markdown content to `.md` file instead of HTML
2. Update `parsed_markdown_path` instead of `parsed_html_path`

Replace the function body (lines 173-220):

```python
async def _save_parsed_content(file_path: Path, parsed_data: dict, document: Document, settings, document_id: str) -> dict:
    """Save parsed content to disk and update document record."""
    # Publish saving progress: start
    _publish_parse_progress(document_id, "saving", 1, 3, 0)

    parsed_dir = file_path.parent
    suffix = file_path.suffix.lower()

    # For DOCX/DOC: save as Markdown
    if suffix in [".docx", ".doc"]:
        md_path = parsed_dir / f"{file_path.stem}_parsed.md"
        images_dir = parsed_dir / f"{file_path.stem}_images"

        md_content = parsed_data["text"]

        # Save Markdown content
        md_path.write_text(md_content, encoding="utf-8")

        # Handle images - copy from markitdown result
        if parsed_data["images"]:
            images_dir.mkdir(exist_ok=True)
            for img_info in parsed_data["images"]:
                img_path = images_dir / img_info["filename"]
                if not img_path.exists():
                    img_path.write_bytes(img_info["data"])
            document.parsed_images_dir = str(images_dir)

        document.parsed_markdown_path = str(md_path)
        document.word_count = len(md_content.split())
        document.status = "parsed"

        # Publish saving progress: complete
        _publish_parse_progress(document_id, "saving", 3, 3, 0)

        return {
            "status": "success",
            "document_id": document.id,
            "parsed_markdown_path": str(md_path),
            "page_count": None,
            "word_count": document.word_count,
        }

    # For PDF: keep existing HTML logic
    elif suffix == ".pdf":
        html_path = parsed_dir / f"{file_path.stem}_parsed.html"
        images_dir = parsed_dir / f"{file_path.stem}_images"

        html_content = parsed_data["text"]

        # Fix image paths in HTML to point to the images directory
        images_dir_name = f"{file_path.stem}_images"
        has_images = parsed_data["images"] or images_dir.exists()
        if has_images:
            html_content = _fix_html_image_paths(html_content, images_dir_name)
            html_content = _insert_missing_img_tags(html_content, images_dir)

        # Save images
        if parsed_data["images"]:
            images_dir.mkdir(exist_ok=True)
            for img_info in parsed_data["images"]:
                img_path = images_dir / img_info["filename"]
                img_path.write_bytes(img_info["data"])
            document.parsed_images_dir = str(images_dir)

        # Write HTML and update document
        html_path.write_text(html_content, encoding="utf-8")
        document.parsed_html_path = str(html_path)
        document.page_count = parsed_data.get("page_count")
        document.word_count = len(html_content.split())
        document.status = "parsed"

        # Publish saving progress: complete
        _publish_parse_progress(document_id, "saving", 3, 3, 0)

        return {
            "status": "success",
            "document_id": document.id,
            "parsed_html_path": str(html_path),
            "page_count": document.page_count,
            "word_count": document.word_count,
        }
```

- [ ] **Step 3: 提交**

```bash
git add backend/tasks/document_parser.py
git commit -m "feat: integrate markitdown into document parsing tasks

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: 更新 Documents API

**Files:**
- Modify: `backend/api/documents.py:163-206` (get_document_content function)

- [ ] **Step 1: 修改 get_document_content 返回 Markdown 内容**

Replace the `get_document_content` function body (lines 136-206):

```python
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

    # Determine content format based on file extension
    file_ext = Path(document.file_path).suffix.lower()

    content = ""
    content_format = "html"
    images = []

    if file_ext in [".docx", ".doc"]:
        # Return Markdown content
        content_format = "markdown"
        if document.parsed_markdown_path and Path(document.parsed_markdown_path).exists():
            content = Path(document.parsed_markdown_path).read_text(encoding="utf-8")

        # Get image paths
        workspace_dir = settings.workspace_path
        if document.parsed_images_dir and Path(document.parsed_images_dir).exists():
            for p in Path(document.parsed_images_dir).iterdir():
                if p.is_file():
                    rel_path = p.relative_to(workspace_dir)
                    images.append(f"/files/{rel_path}")

    else:
        # PDF: return HTML content (existing logic)
        if document.parsed_html_path and Path(document.parsed_html_path).exists():
            html_content = Path(document.parsed_html_path).read_text(encoding="utf-8")

            # Get image paths and fix HTML image src paths
            workspace_dir = settings.workspace_path
            workspace_rel_path = ""
            if document.parsed_images_dir and Path(document.parsed_images_dir).exists():
                for p in Path(document.parsed_images_dir).iterdir():
                    if p.is_file():
                        rel_path = p.relative_to(workspace_dir)
                        images.append(f"/files/{rel_path}")
                workspace_rel_path = Path(document.parsed_images_dir).relative_to(workspace_dir).parent

            # Fix relative image paths in HTML to use /files/ URLs
            if workspace_rel_path:
                import re

                def fix_img_src(match):
                    img_tag = match.group(0)
                    src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
                    if not src_match:
                        return img_tag
                    src = src_match.group(1)
                    if src.startswith(('http://', 'https://', '/')):
                        return img_tag
                    new_src = f"/files/{workspace_rel_path}/{src}"
                    return img_tag.replace(f'"{src}"', f'"{new_src}"').replace(f"'{src}'", f"'{new_src}'")

                html_content = re.sub(r'<img[^>]+>', fix_img_src, html_content)

            content = html_content

    return DocumentContentResponse(content=content, images=images, format=content_format)
```

- [ ] **Step 2: 提交**

```bash
git add backend/api/documents.py
git commit -m "feat: update documents API to return Markdown content

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: 修改前端文档查看组件

**Files:**
- Modify: `frontend/src/views/ProjectView.vue:273-281`

- [ ] **Step 1: 安装 vue-markdown 依赖**

Run:
```bash
cd /home/openclaw/bjt_agent/frontend && npm install @vc/vue-markdown
```

- [ ] **Step 2: 修改 ProjectView.vue 导入和使用**

Add import at top of `<script setup>` section:
```typescript
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import VueMarkdown from '@vc/vue-markdown'
```

Replace the doc viewer body rendering (lines 273-281):

```vue
<div class="doc-viewer-body">
  <div v-if="docViewerLoading" class="loading">正在加载文档...</div>
  <div v-else-if="docViewerContent" class="doc-content">
    <!-- Markdown 渲染 (DOCX/DOC) -->
    <VueMarkdown
      v-if="docViewerContent.format === 'markdown'"
      :source="docViewerContent.content"
      class="markdown-content"
    />
    <!-- HTML 渲染 (PDF) -->
    <div
      v-else-if="docViewerContent.content"
      class="html-content"
      v-html="DOMPurify.sanitize(docViewerContent.content)"
    />
    <div v-else class="no-content">无内容</div>
  </div>
</div>
```

Add Markdown styles to `<style>` section:

```css
.markdown-content {
  line-height: 1.6;
  color: var(--text-primary);
}

.markdown-content h1,
.markdown-content h2,
.markdown-content h3 {
  margin-top: 1.5em;
  margin-bottom: 0.5em;
  font-weight: 600;
}

.markdown-content h1 { font-size: 1.75em; }
.markdown-content h2 { font-size: 1.5em; }
.markdown-content h3 { font-size: 1.25em; }

.markdown-content p {
  margin-bottom: 1em;
}

.markdown-content ul,
.markdown-content ol {
  margin-bottom: 1em;
  padding-left: 2em;
}

.markdown-content li {
  margin-bottom: 0.5em;
}

.markdown-content img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1em 0;
}

.markdown-content table {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 1em;
}

.markdown-content th,
.markdown-content td {
  border: 1px solid #ddd;
  padding: 8px;
}

.markdown-content th {
  background-color: #f5f5f5;
  font-weight: 600;
}

.markdown-content code {
  background-color: #f5f5f5;
  padding: 0.2em 0.4em;
  border-radius: 3px;
  font-size: 0.9em;
}

.markdown-content pre {
  background-color: #f5f5f5;
  padding: 1em;
  border-radius: 6px;
  overflow-x: auto;
  margin-bottom: 1em;
}

.markdown-content pre code {
  background: none;
  padding: 0;
}
```

- [ ] **Step 3: 提交**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "feat: add Markdown rendering support to document viewer

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: 测试验证

**Files:**
- Create: `backend/tests/test_parsers/test_markitdown_converter.py`

- [ ] **Step 1: 创建 markitdown converter 单元测试**

Create `backend/tests/test_parsers/test_markitdown_converter.py`:

```python
"""Tests for MarkitdownConverter."""

import pytest
from pathlib import Path
from backend.parsers.markitdown_converter import (
    MarkitdownConverter,
    MarkitdownConversionError,
    ConversionResult,
    ImageInfo,
)


class TestMarkitdownConverter:
    """Test cases for MarkitdownConverter."""

    def test_converter_initialization(self):
        """Test converter can be initialized."""
        converter = MarkitdownConverter()
        assert converter.timeout == 300

    def test_unsupported_file_type(self):
        """Test that unsupported file types raise ValueError."""
        converter = MarkitdownConverter()
        with pytest.raises(ValueError) as exc_info:
            converter.convert(Path("/nonexistent/file.pdf"))
        assert "Unsupported file type" in str(exc_info.value)

    def test_nonexistent_file(self):
        """Test that nonexistent files raise FileNotFoundError."""
        converter = MarkitdownConverter()
        with pytest.raises(FileNotFoundError):
            converter.convert(Path("/nonexistent/document.docx"))

    @pytest.mark.integration
    def test_convert_sample_docx(self, tmp_path):
        """Integration test: convert a sample DOCX file."""
        # This test requires a real DOCX file
        # Skip if no sample file available
        pytest.skip("Requires sample DOCX file")
```

Run tests:
```bash
cd /home/openclaw/bjt_agent && conda activate py311 && pytest backend/tests/test_parsers/test_markitdown_converter.py -v
```

- [ ] **Step 2: 手动测试**

1. 启动服务:
```bash
./scripts/bjt.sh start
```

2. 上传一个 DOCX 文件，验证:
   - 解析成功
   - 查看文档时 Markdown 正确渲染
   - 图片正确显示

- [ ] **Step 3: 提交测试**

```bash
git add backend/tests/test_parsers/test_markitdown_converter.py
git commit -m "test: add markitdown converter unit tests

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## 实施检查清单

- [ ] Task 1: markitdown submodule 已添加
- [ ] Task 2: MarkitdownConverter 类已创建
- [ ] Task 3: Document 模型已更新
- [ ] Task 4: 解析任务已修改
- [ ] Task 5: API 已更新
- [ ] Task 6: 前端已修改
- [ ] Task 7: 测试验证通过
