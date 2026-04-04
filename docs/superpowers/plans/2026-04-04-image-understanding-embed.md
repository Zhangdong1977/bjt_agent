# 图片识别结果嵌入 md 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在文档解析时，将图片识别结果以 `图片内容: xxx` 格式插入到 md 文件中对应图片链接的下方。

**Architecture:** 修改 `_save_parsed_content` 函数，在图片描述处理后、正则替换 md 中的图片链接，将描述嵌入到图片位置下方。

**Tech Stack:** Python, re (正则), pathlib

---

## 文件结构

- **修改**: `backend/tasks/document_parser.py` — 修改 `_save_parsed_content` 函数
- **新增测试**: `backend/tests/test_image_embed_md.py` — 单元测试

---

## Task 1: 编写图片嵌入 md 的单元测试

**Files:**
- Create: `backend/tests/test_image_embed_md.py`

- [ ] **Step 1: 编写测试文件**

```python
"""Tests for embedding image descriptions into markdown."""

import pytest
from backend.tasks.document_parser import _embed_image_descriptions_in_md


class TestEmbedImageDescriptions:
    """Test _embed_image_descriptions_in_md function."""

    def test_single_image_with_description(self):
        """Single image with description should embed description below."""
        md = "Some text\n![image](test_images/photo.png)\nMore text"
        desc_map = {"photo.png": "A photo of a sunset"}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert result == (
            "Some text\n"
            "![image](test_images/photo.png)\n"
            "图片内容: A photo of a sunset\n"
            "More text"
        )

    def test_multiple_images_with_descriptions(self):
        """Multiple images each get their description below."""
        md = "Start\n![image](img1.png)\nMiddle\n![image](img2.jpg)\nEnd"
        desc_map = {
            "img1.png": "First image description",
            "img2.jpg": "Second image description",
        }

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert "![image](img1.png)\n图片内容: First image description" in result
        assert "![image](img2.jpg)\n图片内容: Second image description" in result

    def test_image_without_description_unchanged(self):
        """Image without matching description keeps original format."""
        md = "Text\n![image](unknown.png)\nMore"
        desc_map = {"other.png": "Some description"}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert result == "Text\n![image](unknown.png)\nMore"

    def test_empty_description_map(self):
        """Empty description map keeps all images unchanged."""
        md = "![image](photo.png)\n![image](diagram.gif)"
        desc_map = {}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert result == md

    def test_partial_descriptions(self):
        """Some images with descriptions, some without."""
        md = "![image](a.png)\n![image](b.png)\n![image](c.png)"
        desc_map = {
            "a.png": "Description A",
            "c.png": "Description C",
        }

        result = _embed_image_descriptions_in_md(md, desc_map)

        lines = result.split("\n")
        # a.png should have description
        a_idx = next(i for i, l in enumerate(lines) if "![image](a.png)" in l)
        assert lines[a_idx + 1] == "图片内容: Description A"
        # b.png should not have description
        b_idx = next(i for i, l in enumerate(lines) if "![image](b.png)" in l)
        assert lines[b_idx + 1] == ""
        # c.png should have description
        c_idx = next(i for i, l in enumerate(lines) if "![image](c.png)" in l)
        assert lines[c_idx + 1] == "图片内容: Description C"

    def test_filename_extraction_from_path(self):
        """Extracts filename from full path for matching."""
        md = "![image](RTCMS技术规范书_20260404115542_images/page_1.png)"
        desc_map = {"page_1.png": "Page one content"}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert "图片内容: Page one content" in result

    def test_description_with_special_characters(self):
        """Descriptions with special characters are preserved."""
        md = "![image](x.png)"
        desc_map = {"x.png": "图表展示了 MCU/SFU 双模式工作流程 (含 NACK/FEC 机制)"}

        result = _embed_image_descriptions_in_md(md, desc_map)

        assert "图片内容: 图表展示了 MCU/SFU 双模式工作流程 (含 NACK/FEC 机制)" in result
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/openclaw/bjt_agent && conda activate py311 && pytest backend/tests/test_image_embed_md.py -v 2>&1 | head -50`
Expected: ERROR — `_embed_image_descriptions_in_md` not defined

---

## Task 2: 实现图片嵌入函数

**Files:**
- Modify: `backend/tasks/document_parser.py`

- [ ] **Step 1: 在 `_save_parsed_content` 函数上方添加辅助函数**

在 `_save_parsed_content` 函数定义之前（约第15行之后）添加：

```python
def _embed_image_descriptions_in_md(md_content: str, desc_map: dict[str, str]) -> str:
    """Embed image descriptions below their corresponding image links in markdown.

    Args:
        md_content: Markdown content with ![image](path) patterns
        desc_map: Mapping of filename -> description text

    Returns:
        Markdown with descriptions embedded below image links
    """
    def replace_image_match(match):
        image_path = match.group(1)  # e.g., "RTCMS_images/xxx.png"
        filename = Path(image_path).name  # Extract "xxx.png"
        desc = desc_map.get(filename, "")
        if desc:
            return f"{match.group(0)}\n图片内容: {desc}"
        return match.group(0)

    return re.sub(r'!\[image\]\(([^)]+)\)', replace_image_match, md_content)
```

- [ ] **Step 2: 修改 `_save_parsed_content` 函数中的图片处理逻辑**

找到 `_save_parsed_content` 函数中处理图片 LLM 描述的代码块（约第24-37行）：

原代码：
```python
    # Process images with LLM if available
    if parsed_data["images"] and settings.mini_agent_api_key:
        try:
            image_descriptions = await _process_images_with_llm(
                parsed_data["images"],
                settings.mini_agent_api_key,
                settings.mini_agent_api_base,
                settings.mini_agent_model,
            )
            if image_descriptions:
                md_content += "\n\n## Extracted Image Content\n\n"
                md_content += "\n".join([f"- {desc}" for desc in image_descriptions])
        except Exception as e:
            logger.warning(f"Failed to process images with LLM: {e}")
```

替换为：
```python
    # Process images with LLM if available
    if parsed_data["images"] and settings.mini_agent_api_key:
        try:
            image_descriptions = await _process_images_with_llm(
                parsed_data["images"],
                settings.mini_agent_api_key,
                settings.mini_agent_api_base,
                settings.mini_agent_model,
            )
            if image_descriptions:
                # Build filename -> description mapping
                desc_map = {}
                for desc in image_descriptions:
                    # Format: "[Image: filename.png] 描述内容"
                    match = re.match(r'\[Image: ([^\]]+)\] (.+)', desc)
                    if match:
                        desc_map[match.group(1)] = match.group(2)
                # Embed descriptions below corresponding image links
                if desc_map:
                    md_content = _embed_image_descriptions_in_md(md_content, desc_map)
        except Exception as e:
            logger.warning(f"Failed to process images with LLM: {e}")
```

- [ ] **Step 3: 运行测试验证通过**

Run: `cd /home/openclaw/bjt_agent && conda activate py311 && pytest backend/tests/test_image_embed_md.py -v`
Expected: PASS

- [ ] **Step 4: 提交代码**

```bash
git add backend/tasks/document_parser.py backend/tests/test_image_embed_md.py
git commit -m "$(cat <<'EOF'
feat: embed image descriptions below image links in parsed markdown

Instead of appending image descriptions to a separate section at the end,
descriptions are now embedded directly below their corresponding image links
in the format "图片内容: xxx".

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 验证端到端功能

- [ ] **Step 1: 重新解析测试文档**

在浏览器中：
1. 访问 http://localhost:3000
2. 使用 zhangdong/7745duck 登录
3. 创建新项目或上传文档到现有项目
4. 上传 `testdocuments/RTCMS技术规范书.docx` 到招标书区域
5. 等待解析完成（status 变为 parsed）

- [ ] **Step 2: 检查生成的 md 文件**

Run:
```bash
ls -la /home/openclaw/bjt_agent/workspace/*/49fc0cdb-4df9-41f8-a39c-de8c4b85c64a/tender/*_parsed.md
# 或查找最新的项目目录
ls -lt /home/openclaw/bjt_agent/workspace/*/ 2>/dev/null | head -5
```

- [ ] **Step 3: 验证图片描述嵌入**

检查 md 文件内容：
```bash
grep -A1 "图片内容:" /path/to/parsed.md | head -30
```

预期：图片链接下方应有 `图片内容: xxx` 行

- [ ] **Step 4: 提交验证结果**

```bash
git add -A
git commit -m "$(cat <<'EOF'
test: verify image description embedding works end-to-end

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## 验证清单

- [ ] 单图片正确嵌入描述
- [ ] 多图片各自嵌入对应描述
- [ ] 无描述的图片保持原链接不变
- [ ] 部分有描述场景正常处理
- [ ] 从完整路径正确提取文件名进行匹配
