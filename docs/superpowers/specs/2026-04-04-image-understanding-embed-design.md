# 图片识别结果嵌入 md 设计

## 概述

在文档解析时，使用 MiniMax understand_image MCP 工具识别图片内容，并将识别结果以 `图片内容: xxx` 格式插入到 md 文件中对应图片链接的下方。

## 现状问题

- `html_to_markdown` 将图片转为 `![image](path)` 格式
- `_process_images_with_llm` 已实现图片识别，但结果追加到 md 末尾的独立 section
- 用户期望图片识别结果出现在对应图片链接的下方

## 目标格式

```markdown
![image](xxx.png)
图片内容: 图表展示了 MCU 工作方式...

![image](yyy.gif)
图片内容: 流程图显示 NACK 机制工作示意...
```

## 实现方案

### 修改位置

`backend/tasks/document_parser.py` 中的 `_save_parsed_content` 函数。

### 修改逻辑

1. **解析图片识别结果**：调用 `_process_images_with_llm` 获取描述列表，返回格式为 `["[Image: filename] 描述内容", ...]`

2. **构建文件名→描述映射**：
   ```python
   desc_map = {}
   for desc in image_descriptions:
       # 格式: "[Image: filename.png] 描述内容"
       match = re.match(r'\[Image: ([^\]]+)\] (.+)', desc)
       if match:
           desc_map[match.group(1)] = match.group(2)
   ```

3. **替换 md 中的图片链接**：用正则匹配所有 `![image](path)` 模式，在其下方插入描述行
   ```python
   def replace_image_match(match):
       image_path = match.group(1)  # e.g., "RTCMS技术规范书_20260404115542_images/xxx.png"
       filename = Path(image_path).name  # 提取文件名
       desc = desc_map.get(filename, "")
       if desc:
           return f"{match.group(0)}\n图片内容: {desc}"
       return match.group(0)

   md_content = re.sub(r'!\[image\]\(([^)]+)\)', replace_image_match, md_content)
   ```

### 处理失败情况

- 如果 `_process_images_with_llm` 调用失败或返回空，保留原图片链接，不插入描述行
- 如果某个图片在映射中找不到描述，保留原图片链接

### 代码流程

```
_parse_docx / _parse_pdf
    ↓
_save_parsed_content
    ↓
1. 调用 _process_images_with_llm 获取图片描述
    ↓
2. 构建 filename → description 映射字典
    ↓
3. 用正则替换 md 中的 ![image](path)，在下方插入 "图片内容: xxx"
    ↓
4. 保存 md 文件
```

## 关键实现细节

- **文件名匹配**：从完整路径中提取文件名进行匹配（如 `xxx.png`）
- **正则**：`!\[image\]\(([^)]+)\)` 匹配 md 中的图片链接
- **描述格式**：`图片内容: {识别结果}`
- **多图片支持**：遍历处理所有匹配项

## 测试要点

1. 单个图片正确嵌入描述
2. 多个图片各自嵌入对应描述
3. 描述为空的图片不插入内容行
4. 图片识别失败时保留原链接
5. 混合场景：部分图片有描述，部分没有
