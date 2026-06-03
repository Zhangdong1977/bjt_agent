CASE_COMPRESS_PROMPT = """\
你是一名标书审查经验压缩专家。你的任务是将 AgentStep 轨迹压缩为精炼的经验案例四元组。

## 输入

- Agent 步骤轨迹（工具调用 + 结果）：{agent_steps}
- 审查发现：{findings}

## 压缩流程

### 第一阶段：预压缩工具调用
- 保留工具调用的语义信息（调用了什么工具、搜索了什么关键词）
- 丢弃大段返回文本，仅保留关键结论
- 目标压缩率约 10%

### 第二阶段：压缩为四元组
将预压缩结果提炼为以下四个字段：

1. **task_intent**：使用模板 "审查 {rule_doc_name}，发现 {finding_count} 项{severity_summary}"
2. **approach**：必须展示 "使用什么工具搜索什么关键词 → 得到什么 → 判断依据" 的链路
3. **key_insight**：本次审查最核心的方法论洞察
4. **quality_score_llm**：0-1 质量评分，对齐 QualityEvaluator 三个维度：
   - 合规准确性：发现是否正确识别了合规问题
   - 严重度适当性：严重度判定是否合理
   - 完整性：是否遗漏了明显问题

## 已知工具

- DocSearchTool：文档搜索工具
- RAGSearchTool：RAG 检索工具
- ComparatorTool：对比分析工具

## 输出格式

严格输出 JSON，不要输出任何其他内容：
```json
{{
  "task_intent": "审查 xxx，发现 N 项问题",
  "approach": "使用 DocSearchTool 搜索'资质证书' → 找到3处过期证书 → 判定不合规",
  "key_insight": "核心方法论洞察",
  "quality_score_llm": 0.8
}}
```"""
