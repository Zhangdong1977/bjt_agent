# Document Parser Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove AI image understanding from document parsing pipeline to improve parse speed.

**Architecture:** Simplify the parsing pipeline by removing LLM image processing. Keep image file extraction and `<img>` tag embedding. Update progress stages from 3 to 3 new stages (converting/extracting/saving).

**Tech Stack:** Python (FastAPI/Celery), Vue3/TypeScript

---

## File Map

### Backend Changes

| File | Responsibility |
|------|----------------|
| `backend/tasks/document_parser.py` | Core parsing logic - remove LLM calls, update stages |
| `frontend/src/components/DocumentParseProgress.vue` | Progress UI - update stage labels |

---

## Task 1: Update Backend Stage Definition and Remove LLM Functions

**Files:**
- Modify: `backend/tasks/document_parser.py:20`

- [ ] **Step 1: Update Stage type definition**

Change line 20 from:
```python
Stage = Literal["extracting_text", "processing_images", "saving"]
```
To:
```python
Stage = Literal["converting", "extracting", "saving"]
```

- [ ] **Step 2: Remove `_embed_image_descriptions_in_md` function**

Delete lines 79-99 (the entire function).

- [ ] **Step 3: Remove `_embed_image_descriptions_in_html` function**

Delete lines 102-142 (the entire function).

- [ ] **Step 4: Remove `_process_images_with_llm` function**

Delete lines 443-602 (the entire function).

- [ ] **Step 5: Commit**

```bash
git add backend/tasks/document_parser.py
git commit -m "refactor: remove LLM image processing from document parser"
```

---

## Task 2: Update `_save_parsed_content` Function

**Files:**
- Modify: `backend/tasks/document_parser.py:238-308`

- [ ] **Step 1: Remove LLM image processing block**

In `_save_parsed_content()`, delete the entire block:
```python
# Process images with LLM if available
desc_map = {}
logger.info(f"[_save_parsed_content] images count: {len(parsed_data.get('images', []))}, api_key set: {bool(settings.mini_agent_api_key)}")
if parsed_data["images"] and settings.mini_agent_api_key:
    try:
        image_descriptions = await _process_images_with_llm(...)
        ...
    except Exception as e:
        logger.warning(f"Failed to process images with LLM: {e}")
```

This removes lines 246-268.

- [ ] **Step 2: Remove description embedding block**

Delete the block:
```python
# Embed descriptions below corresponding image links in HTML
if desc_map:
    html_content = _embed_image_descriptions_in_html(html_content, desc_map)
```

This removes lines 282-285.

- [ ] **Step 3: Verify remaining logic is intact**

The function should still:
1. Fix image paths (`_fix_html_image_paths`)
2. Insert missing img tags (`_insert_missing_img_tags`)
3. Save images to directory
4. Write HTML file
5. Update document status

- [ ] **Step 4: Commit**

```bash
git add backend/tasks/document_parser.py
git commit -m "refactor: remove description embedding from _save_parsed_content"
```

---

## Task 3: Update Progress Publishing Calls

**Files:**
- Modify: `backend/tasks/document_parser.py:367-380`

- [ ] **Step 1: Update progress publishing in `parse_document` task**

In `parse_document` Celery task, find the progress publishing calls and update stage names:

Change from `extracting_text` to `extracting`:
```python
# Around line 368
_publish_parse_progress(document_id, "extracting", 0, 0, 0)
```

Add `converting` stage before extraction (after status is set to "parsing"):
```python
_publish_parse_progress(document_id, "converting", 0, 0, 0)
```

The `saving` stage call at line 380 is already correct.

- [ ] **Step 2: Commit**

```bash
git add backend/tasks/document_parser.py
git commit -m "refactor: update progress stages to converting/extracting/saving"
```

---

## Task 4: Update Frontend Progress Labels

**Files:**
- Modify: `frontend/src/components/DocumentParseProgress.vue:13-17`

- [ ] **Step 1: Update stageLabels object**

Change from:
```typescript
const stageLabels: Record<string, string> = {
  extracting_text: '正在提取文档内容',
  processing_images: '正在调用 AI 理解图片',
  saving: '正在保存解析结果',
}
```

To:
```typescript
const stageLabels: Record<string, string> = {
  converting: '正在转换格式',
  extracting: '正在提取文本和图片',
  saving: '正在保存解析结果',
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/DocumentParseProgress.vue
git commit -m "refactor: update parse progress labels to match new stages"
```

---

## Verification Steps

After all tasks complete:

1. **Backend verification:**
   ```bash
   cd backend
   python -c "from tasks.document_parser import parse_document; print('Import OK')"
   ```

2. **Frontend verification:**
   ```bash
   cd frontend
   npm run type-check
   ```

3. **Manual test:**
   - Upload a DOCX file
   - Verify progress shows: 正在转换格式 → 正在提取文本和图片 → 正在保存解析结果
   - Verify parsed HTML contains `<img>` tags without description text

---

## Spec Coverage Check

| Spec Requirement | Task |
|------------------|------|
| Remove `_process_images_with_llm` | Task 1 |
| Remove `_embed_image_descriptions_in_html` | Task 1 |
| Remove `_embed_image_descriptions_in_md` | Task 1 |
| New stages: converting/extracting/saving | Task 1, 3 |
| Update frontend labels | Task 4 |
| Keep image file extraction | Verified in Task 2 |
| Keep `<img>` tags | Verified in Task 2 |

All spec requirements covered.
