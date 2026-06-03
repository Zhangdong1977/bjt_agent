SKILL_SUCCESS_EXTRACT_PROMPT = """\
你是一名标书审查技能萃取专家。你的任务是从成功案例（quality_score >= 0.5）中提取可复用的标准操作流程（SOP）。

## 输入

- 案例详情：{case_detail}
- 已有技能内容（如有）：{existing_skill_content}

## 提取规则

1. **Steps 最多 6 步**，每步必须包含"工具名称 + 关键词 + 判断依据"三元组
2. 如果已有技能与新提取内容的重叠度 >= 60%，则 action=update；否则 action=add
3. overlap_ratio 为新内容与已有内容的语义重叠比例（0-1）

## 内容格式

```markdown
## Steps
1. 使用 DocSearchTool 搜索"营业执照有效期" → 找到营业执照副本 → 核实是否在有效期内
2. ...

## Pitfalls（可选）
- 常见误区或注意事项
```

## 输出格式

严格输出 JSON，不要输出任何其他内容：
```json
{{
  "name": "技能名称",
  "description": "技能简要描述",
  "content": "## Steps\\n1. ...\\n\\n## Pitfalls\\n- ...",
  "overlap_ratio": 0.3,
  "action": "add"
}}
```"""
