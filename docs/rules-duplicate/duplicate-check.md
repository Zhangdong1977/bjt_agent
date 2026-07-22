---
version: "1"
chunk_size_chars: 1200
chunk_overlap_chars: 150
lexical_threshold: 0.78
semantic_threshold: 0.82
top_k_per_chunk: 5
max_candidates_per_pair: 100
min_evidence_chars: 50
structure:
  min_real_headings: 3
  require_multiple_levels: false
exclude_section_title_patterns:
  - "^(目录|封面|投标函格式)$"
exclude_text_patterns: []
---

# 查重目标

识别两份投标文件中不属于正常模板、法规引用或行业通用表达的实质性重复内容。结论仅供人工复核，不直接认定围标、串标或违法行为。

# 工作要求

1. 先了解两份文档结构，再选择可能对应或可疑的章节；不能只比较同名章节。
2. 每个可疑项必须同时提供两份文档的原文证据和可定位的章节信息。
3. 不输出综合相似度百分比，也不要凭主观感觉编造精确比例。
4. 没有符合规则的可疑内容时，明确返回“未发现可疑重复”。

# 应排除的合理重复

- 法律法规、国家标准、行业规范的原文或规范名称；
- 招标文件中要求投标人原样响应的条款和固定格式；
- 目录、封面、页眉页脚、签章提示、投标函通用格式；
- 行业通用术语、常规工序名称、不可避免的短语；
- 能够合理解释为双方共同引用公开来源的内容。

# 可疑重复判断

重点关注：大段非公开表述一致、专有措辞或罕见错误一致、步骤顺序和细节高度一致、不同标题下出现实质相同的方案，以及经过轻微改写但结构和独特信息一致的内容。

# 输出要求

输出必须符合系统提供的结构化 Schema。每条 matches 必须包含 title、duplicate_type、document_a_evidence、document_b_evidence 和 analysis。
