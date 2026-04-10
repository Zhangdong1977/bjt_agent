# Markitdown 替换 LibreOffice 文档解析设计方案

## 概述

将项目中的 LibreOffice 文档解析方案替换为 [markitdown](https://github.com/microsoft/markitdown)，实现更轻量、更快速的文档解析。前端展示由 HTML 改为 Markdown 渲染。

## 目标

1. 将 markitdown 添加为 git submodule
2. 替换 LibreOffice 的 DOCX/DOC 解析逻辑
3. 保留 PDF 解析（PyMuPDF）不变
4. 前端改用 vue-markdown 渲染 Markdown 内容
5. 支持图片提取和展示

---

## 1. Git Submodule 配置

### 添加 submodule

```bash
git submodule add https://github.com/microsoft/markitdown.git third_party/markitdown
```

### 目录结构

```
bjt_agent/
├── third_party/
│   └── markitdown/          # markitdown git submodule
├── backend/
│   └── parsers/
│       ├── markitdown_converter.py  # 新增
│       ├── libreoffice_converter.py # 保留（PDF 不依赖）
│       ├── html_parser.py
│       └── markdown_converter.py
```

---

## 2. 后端解析器设计

### 2.1 MarkitdownConverter 类

位置：`backend/parsers/markitdown_converter.py`

```python
from markitdown import MarkItDown

class MarkitdownConverter:
    def __init__(self):
        self.markitdown = MarkItDown()

    def convert(self, doc_path: str) -> ConversionResult:
        """
        将文档转换为 Markdown

        Returns:
            ConversionResult:
                - markdown_content: str  # Markdown 文本
                - images: List[ImageInfo]  # 图片列表
                    - path: str  # 绝对路径
                    - name: str  # 文件名
                - page_count: int | None  # markitdown 不提供页数
        """
        result = self.markitdown.convert(doc_path)

        return ConversionResult(
            markdown_content=result.text_content,
            images=self._extract_images(result),
            page_count=None
        )

    def _extract_images(self, result) -> List[ImageInfo]:
        # markitdown 返回的图片通过相对路径引用
        # 需要提取并复制到 workspace
```

### 2.2 解析流程变更

#### DOCX/DOC 解析（变更）

```
旧流程:
  DOCX → LibreOffice → HTML → 存储 parsed_html.html

新流程:
  DOCX/DOC → markitdown.convert() → Markdown → 存储 parsed.md
```

#### PDF 解析（保持不变）

```
PDF → PyMuPDF → HTML → 存储 parsed_html.html
```

### 2.3 解析任务修改

文件：`backend/tasks/document_parser.py`

```python
# 新增 MarkitdownConverter 导入
from backend.parsers.markitdown_converter import MarkitdownConverter

# 修改 convert_document_content 函数
async def convert_document_content(document: Document, file_path: str):
    # 根据文件类型选择解析器
    if file_path.endswith(('.docx', '.doc')):
        converter = MarkitdownConverter()
        result = converter.convert(file_path)

        # 保存 Markdown 文件
        md_path = file_path.replace(ext, '_parsed.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(result.markdown_content)

        # 处理图片
        images_dir = file_path.replace(ext, '_images')
        copy_images(result.images, images_dir)

        return {
            'markdown_path': md_path,
            'images_dir': images_dir,
            'images': result.images,
            'word_count': len(result.markdown_content)
        }
    elif file_path.endswith('.pdf'):
        # 保持现有 PDF 逻辑
        ...
```

### 2.4 Document 模型变更

文件：`backend/models/document.py`

```python
# 新增字段
class Document(Base):
    # 现有字段...

    # 将 parsed_html_path 替换为 parsed_markdown_path
    parsed_markdown_path = Column(String, nullable=True)  # Markdown 文件路径
    parsed_images_dir = Column(String, nullable=True)  # 图片目录
```

**注意**：为保持向后兼容，可同时保留 `parsed_html_path` 并新增 `parsed_markdown_path` 字段。

### 2.5 API 响应变更

文件：`backend/api/documents.py` 的 `get_document_content`

```python
@router.get("/projects/{project_id}/documents/{document_id}/content")
async def get_document_content(project_id: int, document_id: int):
    # 读取 Markdown 内容
    with open(doc.parsed_markdown_path, 'r', encoding='utf-8') as f:
        markdown_content = f.read()

    return {
        'content': markdown_content,  # Markdown 而非 HTML
        'images': get_image_urls(doc.parsed_images_dir),
        'format': 'markdown'
    }
```

---

## 3. 前端变更设计

### 3.1 安装依赖

```bash
cd frontend
npm install @vc/l建设的/vue-markdown
```

### 3.2 文档查看组件

文件：`frontend/src/views/ProjectView.vue`

```vue
<template>
  <div v-if="showDocViewer" class="modal-overlay">
    <div class="doc-viewer-modal">
      <div class="doc-viewer-body">
        <!-- Markdown 渲染 -->
        <VueMarkdown
          v-if="docViewerContent?.format === 'markdown'"
          :source="docViewerContent.content"
          class="markdown-content"
        />

        <!-- 保留 HTML 渲染（兼容 PDF） -->
        <div
          v-else-if="docViewerContent?.content"
          class="html-content"
          v-html="DOMPurify.sanitize(docViewerContent.content)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import VueMarkdown from '@vc/vue-markdown'
import DOMPurify from 'dompurify'

// 获取文档内容
const docViewerContent = ref(null)

const loadDocumentContent = async (docId) => {
  const response = await api.get(`/projects/${projectId}/documents/${docId}/content`)
  docViewerContent.value = response.data
}
</script>

<style>
.markdown-content {
  /* Markdown 渲染样式 */
  line-height: 1.6;
}

.markdown-content h1,
.markdown-content h2,
.markdown-content h3 {
  margin-top: 1.5em;
  margin-bottom: 0.5em;
}

.markdown-content p {
  margin-bottom: 1em;
}

.markdown-content img {
  max-width: 100%;
  height: auto;
}

.markdown-content table {
  border-collapse: collapse;
  width: 100%;
}

.markdown-content th,
.markdown-content td {
  border: 1px solid #ddd;
  padding: 8px;
}
</style>
```

### 3.3 图片路径处理

后端返回图片 URL 时使用相对路径：

```python
def get_image_urls(images_dir: str) -> List[str]:
    """生成前端可访问的图片 URL"""
    if not images_dir:
        return []

    # images_dir 格式: /path/to/workspace/{project_id}/doc_images/
    # 转换为: /files/{workspace_rel_path}/doc_images/
    rel_path = images_dir.replace(workspace_root, '')
    return [f"/files/{rel_path}/{img}" for img in os.listdir(images_dir)]
```

前端 Markdown 中的图片引用需内联处理（可选增强）：

```vue
<VueMarkdown
  :source="processImagePaths(docViewerContent.content, docViewerContent.images)"
/>
```

---

## 4. 数据迁移

### 4.1 现有 HTML 文档处理

对于已解析的 HTML 文档，有两种策略：

**策略 A：保留现有 HTML，新增文档使用 Markdown**
- 优点：无需迁移，现有文档正常工作
- 缺点：两套渲染逻辑并存

**策略 B：一次性迁移**
- 优点：统一技术栈
- 缺点：迁移成本高

**推荐策略 A**，前端同时支持 HTML 和 Markdown 渲染。

---

## 5. 错误处理

### 5.1 markitdown 解析失败

```python
try:
    result = self.markitdown.convert(file_path)
except Exception as e:
    logger.error(f"markitdown 解析失败: {file_path}, error: {e}")
    raise DocumentParseError(f"文档解析失败: {str(e)}")
```

### 5.2 不支持的格式

markitdown 支持格式：
- DOCX ✅
- DOC（部分支持，取决于 LibreOffice）
- PDF ✅
- PPTX ✅
- XLSX ✅
- 图片（OCR）✅

对于 markitdown 不支持的格式，回退到原有方案或返回错误。

---

## 6. 测试计划

### 6.1 单元测试

```python
# backend/tests/test_parsers/test_markitdown_converter.py
def test_convert_docx():
    converter = MarkitdownConverter()
    result = converter.convert('test.docx')

    assert result.markdown_content is not None
    assert '```' in result.markdown_content or '#' in result.markdown_content

def test_extract_images():
    # 测试图片提取
```

### 6.2 集成测试

- 上传真实 DOCX/DOC 文件，验证 Markdown 输出
- 验证图片正确提取和路径处理
- 验证前端 Markdown 渲染正确

---

## 7. 实现步骤

1. **添加 git submodule**
   ```bash
   git submodule add https://github.com/microsoft/markitdown.git third_party/markitdown
   ```

2. **创建 MarkitdownConverter 类**
   - 文件：`backend/parsers/markitdown_converter.py`
   - 实现 `convert()` 方法
   - 实现图片提取逻辑

3. **修改文档解析任务**
   - 文件：`backend/tasks/document_parser.py`
   - 集成 MarkitdownConverter
   - 保存 Markdown 文件

4. **更新 Document 模型**
   - 添加 `parsed_markdown_path` 字段
   - 数据库迁移

5. **更新 API 响应**
   - 文件：`backend/api/documents.py`
   - 返回 Markdown 内容

6. **前端依赖安装**
   ```bash
   cd frontend && npm install @vc/vue-markdown
   ```

7. **前端文档查看组件**
   - 文件：`frontend/src/views/ProjectView.vue`
   - 添加 VueMarkdown 组件
   - 处理 Markdown/HTML 格式兼容

8. **测试验证**
   - 单元测试
   - 手动测试上传和预览

---

## 8. 风险与注意事项

| 风险 | 缓解措施 |
|------|---------|
| markitdown 对 DOC 格式支持不稳定 | 保留 LibreOffice 作为 fallback |
| 图片路径处理复杂 | 充分测试各种文档结构 |
| 前端 Markdown 样式不一致 | 定义清晰的 Markdown CSS 样式 |
