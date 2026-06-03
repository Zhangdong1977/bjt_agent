SKILL_FAILURE_EXTRACT_PROMPT = """\
你是一名标书审查失败经验萃取专家。你的任务是从失败案例（quality_score < 0.5）中提取教训，形成可改进的技能。

## 输入

- 案例详情：{case_detail}
- 已有技能内容（如有）：{existing_skill_content}

## 提取规则

1. **内容必须包含 ## Potential Steps 和 ## Pitfalls 两个章节**（均为必填）
2. Pitfalls 必须可追溯到具体的 ReviewResult 或 AgentStep，不能是泛泛而谈
3. Potential Steps 是基于失败教训提出的改进步骤
4. 如果已有技能与新提取内容的重叠度 >= 60%，则 action=update；否则 action=add
5. 从失败案例生成的新技能：skill_form=hypothesis，confidence=0.5

## 内容格式

```markdown
## Potential Steps
1. 改进步骤（包含工具名称 + 关键词 + 判断依据三元组）
2. ...

## Pitfalls
- 具体陷阱描述（引用自 ReviewResult: xxx 或 AgentStep: xxx）
- ...
```

## 输出格式

严格输出 JSON，不要输出任何其他内容：
```json
{{
  "name": "技能名称",
  "description": "技能简要描述",
  "content": "## Potential Steps\\n1. ...\\n\\n## Pitfalls\\n- ...",
  "overlap_ratio": 0.2,
  "action": "add"
}}
```"""
