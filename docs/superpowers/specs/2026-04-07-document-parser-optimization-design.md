# 文档解析优化设计

## 变更概述

取消文档解析阶段的图片 AI 识别功能，提升文档解析速度。

## 背景

当前文档解析流程中，图片 AI 识别（`_process_images_with_llm`）消耗大量时间：
- 10页DOCX（5张图）：~30-60秒
- 50页PDF（20张图）：~2-5分钟

由于用户反馈解析速度过慢，决定取消图片 AI 识别，保留图片文件引用。

## 核心变更

### 移除内容

1. `_process_images_with_llm()` 函数
2. `_embed_image_descriptions_in_html()` 函数
3. `_embed_image_descriptions_in_md()` 函数
4. `processing_images` 进度阶段
5. LLM API 调用及相关逻辑

### 保留内容

1. 图片文件提取和保存（`<img>` 标签保留）
2. 文档文本提取
3. HTML 保存

## 详细设计

### 后端变更

#### 1. `backend/tasks/document_parser.py`

**进度阶段定义变更：**

```python
# 旧
Stage = Literal["extracting_text", "processing_images", "saving"]

# 新
Stage = Literal["converting", "extracting", "saving"]
```

**阶段说明：**
- `converting` - LibreOffice 转换 DOCX/DOC 格式
- `extracting` - 提取文本和图片
- `saving` - 保存解析结果

**进度发布点：**
- `converting` - LibreOffice 转换开始时发布
- `extracting` - 文本/图片提取开始时发布
- `saving` - 开始保存解析结果时发布

**移除的函数：**
- `_process_images_with_llm()` (443-602行)
- `_embed_image_descriptions_in_md()` (79-99行)
- `_embed_image_descriptions_in_html()` (102-142行)

**修改 `_save_parsed_content()` 函数：**
- 移除 LLM 调用逻辑（247-268行）
- 移除嵌入描述逻辑（282-285行）
- 保留图片路径修复和 img 标签插入逻辑

**修改 `parse_document` Celery 任务：**
- 更新进度发布调用为新阶段

#### 2. `backend/parsers/__init__.py`

无需变更（导出的接口不变）

### 前端变更

#### `frontend/src/components/DocumentParseProgress.vue`

**阶段标签更新：**

```typescript
// 旧
const stageLabels: Record<string, string> = {
  extracting_text: '正在提取文档内容',
  processing_images: '正在调用 AI 理解图片',
  saving: '正在保存解析结果',
}

// 新
const stageLabels: Record<string, string> = {
  converting: '正在转换格式',
  extracting: '正在提取文本和图片',
  saving: '正在保存解析结果',
}
```

## 数据流变更

### 优化前
```
上传 → 解析文本 → 解析图片 → LLM识别图片 → 保存HTML → 完成
         ↓           ↓            ↓
    extracting   processing    saving
     _text       _images
```

### 优化后
```
上传 → 转换格式 → 提取文本和图片 → 保存HTML → 完成
            ↓           ↓            ↓
       converting   extracting   saving
```

## 预期效果

| 文档类型 | 优化前耗时 | 优化后耗时 |
|---------|-----------|-----------|
| 10页DOCX（5张图） | ~30-60秒 | ~5-10秒 |
| 50页PDF（20张图） | ~2-5分钟 | ~20-30秒 |

## 测试要点

1. DOCX 文档解析流程完成，进度阶段正确显示
2. PDF 文档解析流程完成，进度阶段正确显示
3. 图片正确保存到 `_images` 目录
4. HTML 中 `<img>` 标签正确引用图片
5. 前端进度组件正确显示 `converting` / `extracting` / `saving` 阶段
6. 文档内容查看功能正常（图片可正常显示）

## 影响范围

- `backend/tasks/document_parser.py` - 核心解析逻辑
- `frontend/src/components/DocumentParseProgress.vue` - 进度显示组件
