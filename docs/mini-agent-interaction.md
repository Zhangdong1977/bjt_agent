# BidReviewAgent 与 Mini-Agent 交互文档

## 概述

`BidReviewAgent` 是基于 `Mini-Agent` 构建的领域定制智能体，用于标书审查（招标书 vs 投标书）。它继承 `mini_agent.agent.Agent` 基类，并扩展了4个自定义工具。

---

## 1. 继承关系与核心扩展

### 1.1 类继承

```
mini_agent.agent.Agent (基类)
    └── backend.agent.bid_review_agent.BidReviewAgent (子类)
```

### 1.2 核心扩展点

| 扩展点 | 说明 |
|--------|------|
| `__init__` | 接收项目/文档路径，初始化 LLM 客户端和自定义工具 |
| `run_review()` | 核心异步方法：加载规则 → 构建提示词 → 调用 `Agent.run()` → 后处理 |
| `_post_process()` | 从 Agent 输出的 markdown 文件提取结构化 findings |
| 自定义工具 | `DocSearchTool`, `RAGSearchTool`, `ComparatorTool`, `MergeDeciderTool` |

---

## 2. 工具注册

### 2.1 自定义工具 (BidReviewAgent 独有)

| 工具名 | 类 | 功能 |
|--------|-----|------|
| `search_tender_doc` | `DocSearchTool` | 读取招标书/投标书内容，支持关键词搜索 |
| `rag_search` | `RAGSearchTool` | 查询企业知识库（RAG） |
| `compare_bid` | `ComparatorTool` | LLM 对比招标要求与投标内容 |
| `merge_decide` | `MergeDeciderTool` | LLM 决策多个相似 finding 是否合并 |

### 2.2 Mini-Agent 内置工具

| 工具名 | 类 | 功能 |
|--------|-----|------|
| `write_file` | `WriteTool` | 向 workspace 写入文件 |
| `read_file` | `ReadTool` | 读取 workspace 文件 |

### 2.3 MCP 工具 (可选)

通过 `load_mcp_tools_async()` 加载 `mcp.json` 配置的 MCP 服务器工具。

---

## 3. 消息格式

### 3.1 Message Schema

```python
class Message(BaseModel):
    role: str                      # "system" | "user" | "assistant" | "tool"
    content: str | list[dict]      # 消息内容
    thinking: str | None = None    # 思维链内容（仅 assistant）
    tool_calls: list[ToolCall] | None = None   # 工具调用列表（仅 assistant）
    tool_call_id: str | None = None  # 工具调用 ID（仅 tool）
    name: str | None = None         # 工具名（仅 tool）
```

### 3.2 LLMResponse Schema

```python
class LLMResponse(BaseModel):
    content: str                    # 文本响应
    thinking: str | None = None      # 思维链（reasoning_split=True 时返回）
    tool_calls: list[ToolCall] | None = None
    finish_reason: str
    usage: TokenUsage | None = None
```

### 3.3 ToolCall Schema

```python
class ToolCall(BaseModel):
    id: str
    type: str  # "function"
    function: FunctionCall

class FunctionCall(BaseModel):
    name: str
    arguments: dict[str, Any]
```

---

## 4. Agent 执行循环（Mini-Agent 基类）

```
Agent.run() 执行流程：

1. 初始化
   └── self.logger.start_new_run()
   └── self.messages = [system_msg, ...]

2. 主循环 (while step < max_steps)
   ├── step_start 事件
   ├── 取消检查 (cancel_event)
   ├── 消息历史摘要检查 (_summarize_messages)
   │   └── 如果 token 超过 token_limit，调用 LLM 摘要历史
   │
   ├── 调用 LLM
   │   └── llm_client.generate(messages, tools)
   │       └── 返回 LLMResponse (content + optional tool_calls)
   │
   ├── llm_output 事件
   │
   ├── 如果无 tool_calls → 任务完成，返回 content
   │
   └── 工具执行 (for tool_call in tool_calls)
       ├── tool_call_start 事件
       ├── tool.execute(**arguments)
       │   └── 返回 ToolResult
       ├── tool_call_end 事件
       └── 添加 tool 消息到 history
```

---

## 5. BidReviewAgent 核心流程

### 5.1 初始化 (`__init__`)

```python
def __init__(self, project_id, tender_doc_path, bid_doc_path,
             user_id, rule_doc_path, event_callback=None,
             logger=None, max_steps=100):

    # 1. 保存项目配置
    self.project_id = project_id
    self.tender_doc_path = tender_doc_path
    self.bid_doc_path = bid_doc_path
    self.user_id = user_id
    self.rule_doc_path = rule_doc_path

    # 2. 初始化 LLM 客户端 (MiniMax OpenAI 兼容协议)
    llm_client = LLMClient(
        api_key=settings.mini_agent_api_key,
        provider=LLMProvider.OPENAI,
        api_base=settings.mini_agent_api_base,
        model=settings.mini_agent_model,
    )

    # 3. 设置 workspace 目录
    workspace_dir = settings.workspace_path / user_id / project_id

    # 4. 注册工具
    tools = [
        DocSearchTool(tender_doc_path, bid_doc_path),
        RAGSearchTool(user_id),
        ComparatorTool(),
        MergeDeciderTool(),
        WriteTool(workspace_dir),
        ReadTool(workspace_dir),
    ]

    # 5. 调用父类初始化
    super().__init__(
        llm_client=llm_client,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        workspace_dir=str(workspace_dir),
        max_steps=max_steps,
        event_callback=event_callback,
    )
```

### 5.2 异步初始化 (`initialize`)

```python
async def initialize(self):
    # 加载 MCP 工具（MiniMax-Coding-Plan-MCP）
    mcp_config_path = Path(__file__).parent.parent / "mcp.json"
    if mcp_config_path.exists():
        mcp_tools = await load_mcp_tools_async(str(mcp_config_path))
        for tool in mcp_tools:
            self.tools[tool.name] = tool
```

### 5.3 运行审查 (`run_review`)

```python
async def run_review(self) -> list[dict]:
    # 1. 加载规则文档
    rule_doc_content = self._load_rule_doc()

    # 2. 构建系统提示词（嵌入规则内容）
    system_prompt = self._build_system_prompt(rule_doc_content)
    self.system_prompt = system_prompt
    self.messages[0] = Message(role="system", content=system_prompt)

    # 3. 添加任务提示词
    output_md_path = str(self.workspace_dir / f"review_{int(time.time())}.md")
    task = f"""请执行投标文件审查任务：
    - 招标书路径: {self.tender_doc_path}
    - 投标书路径: {self.bid_doc_path}
    - 审查结果输出文件: {output_md_path}

    请按照系统提示词中的规则执行审查，并将结果直接写入到上述 md 文件中。
    重要：必须使用 WriteTool 将审查结果写入文件。"""
    self.add_user_message(task)

    # 4. 调用父类 Agent.run() - 重用 Mini-Agent 循环
    await self.run()

    # 5. 后处理：从 markdown 文件提取 findings
    findings = await self._post_process(output_md_path)
    return findings
```

---

## 6. 工具详解

### 6.1 DocSearchTool (`search_tender_doc`)

**参数：**
```json
{
  "doc_type": "tender" | "bid",  // 必需
  "query": "string",              // 可选，关键词搜索
  "chunk": 0,                     // 可选，大文档分页
  "full_content": false            // 可选，返回完整内容
}
```

**返回：**
```python
ToolResult(
    success=True,
    content="友好的可读文本",
    data={
        "line_count": 123,
        "chunk": 0,
        "total_chunks": 1,
        "current_chunk_lines": 123,
        "full_content": "...",
        "query_matches": 5,
    }
)
```

### 6.2 RAGSearchTool (`rag_search`)

**参数：**
```json
{
  "query": "string",  // 必需
  "limit": 5           // 可选，默认 5
}
```

**返回：**
```python
ToolResult(
    success=True,
    content="Knowledge Base Results:\n\n[1] Source: xxx (relevance: 0.95)\n    Content: ...",
    data={
        "results": [...],
        "count": 3,
        "query": "...",
        "has_knowledge": True,
    }
)
```

### 6.3 ComparatorTool (`compare_bid`)

**参数：**
```json
{
  "requirement": "string",  // 必需，招标要求
  "bid_content": "string",  // 必需，投标内容
  "severity": "major"       // 可选，默认 major
}
```

**返回：**
```python
ToolResult(
    success=True,
    content="✅ 满足要求\n或\n❌ 不满足要求（严重程度：严重）\n📝 解释...\n💡 建议...",
    data={
        "requirement": "...",
        "bid_content": "...",
        "is_compliant": False,
        "severity": "critical",
        "explanation": "...",
        "suggestion": "...",
        "location_page": 5,
        "location_line": 23,
    }
)
```

### 6.4 MergeDeciderTool (`merge_decide`)

**参数：**
```json
{
  "new_finding": {...},       // 新发现
  "existing_findings": [...]   // 现有发现列表
}
```

**返回：**
```python
ToolResult(
    success=True,
    content="Should merge / Should not merge / ...",  # LLM 决策文本
    data={...}
)
```

---

## 7. 系统提示词

### 7.1 基础 SYSTEM_PROMPT

包含核心任务定义：理解招标要求 → 查询企业知识库 → 审查投标文件 → 比对分析 → 输出结果。

### 7.2 带规则的 SYSTEM_PROMPT_WITH_RULE

```python
SYSTEM_PROMPT_WITH_RULE = """你是一个专业的投标文件合规审查专家...
## 规则文件完整内容
{rule_doc_content}

## 审查要求
1. 仔细阅读规则文件中的所有检查项
2. 对每个检查项，在投标文件中查找对应的证明材料
3. 对比规则要求与实际提供的内容，判断是否合规
4. 识别所有不符合项，并给出详细说明

## 输出要求
将审查结果保存到 Markdown 文件，每个检查结果格式如下：

## 检查项1: {{检查项名称}}
### 规则项
{{检查项规则描述}}
...
"""
```

---

## 8. LLM 调用细节

### 8.1 OpenAI 兼容协议

```python
# 使用 OpenAI 兼容客户端连接 MiniMax
llm_client = LLMClient(
    api_key=settings.mini_agent_api_key,
    provider=LLMProvider.OPENAI,
    api_base=settings.mini_agent_api_base,
    model=settings.mini_agent_model,  # MiniMax-M2.7-highspeed
)

# 请求参数
{
    "model": "MiniMax-M2.7-highspeed",
    "messages": [...],  # OpenAI 格式
    "extra_body": {"reasoning_split": True},  # 启用思维链分离
    "tools": [...]      # OpenAI tool format
}
```

### 8.2 reasoning_split 机制

Mini-Agent 启用 `reasoning_split=True`，使 LLM 响应分为：
- `reasoning_details`: 思维链内容 → 存入 `Message.thinking`
- `content`: 最终回答内容 → 存入 `Message.content`

两者都存入消息历史，下一轮调用时会一并发送，以保持思维连贯性。

---

## 9. 事件回调 (SSE)

BidReviewAgent 通过 `event_callback` 发送 SSE 事件：

| 事件名 | 触发时机 | 数据 |
|--------|----------|------|
| `step_start` | 每步开始 | `{"step": N, "max_steps": 100}` |
| `llm_output` | LLM 响应后 | `{"step": N, "thinking": "...", "content": "...", "tool_calls": [...]}` |
| `tool_call_start` | 工具执行前 | `{"step": N, "tool": "name", "arguments": {...}}` |
| `tool_call_end` | 工具执行后 | `{"step": N, "tool": "name", "success": bool, "result": "...", "error": "..."}` |
| `step_complete` | 步骤完成后 | `{"step": N}` |
| `completed` | 任务完成 | `{"step": N, "reason": "no_tool_calls"}` |
| `cancelled` | 用户取消 | `{"step": N}` |
| `max_steps_reached` | 达到最大步数 | `{"step": N, "max_steps": 100}` |

---

## 10. 执行流程图

```
用户调用 run_review()
    │
    ▼
┌─────────────────────────────────────┐
│ 1. _load_rule_doc()                 │
│    读取规则文件内容                  │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 2. _build_system_prompt()           │
│    嵌入规则到系统提示词              │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 3. 添加用户任务消息                  │
│    (包含输出文件路径)                │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 4. Agent.run()                      │
│    └── Mini-Agent 执行循环           │
│        ├── LLM.generate()           │
│        │   └── MiniMax API          │
│        ├── 工具调用                  │
│        │   ├── search_tender_doc    │
│        │   ├── rag_search           │
│        │   ├── compare_bid          │
│        │   └── write_file           │
│        └── 事件回调 (SSE)            │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 5. _post_process()                 │
│    └── 从 markdown 提取 findings    │
└─────────────────────────────────────┘
    │
    ▼
返回 list[dict] findings
```

---

## 11. Finding 数据结构

```python
{
    "requirement_key": "req_001",      # 发现项唯一标识
    "requirement_content": "...",      # 招标书要求原文
    "bid_content": "...",               # 投标书对应内容
    "is_compliant": False,             # 是否合规
    "severity": "major",               # 严重程度 (critical/major/minor)
    "location_page": 5,                # 页码（可选）
    "location_line": 23,               # 行号（可选）
    "suggestion": "...",               # 改进建议
    "explanation": "...",              # 详细说明
}
```

---

## 12. 重试机制

`BidReviewAgent` 在 LLM 调用失败时自动重试：

```python
async def _call_llm_with_retry(self, messages, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            response = await self.llm_client.generate(messages=messages)
            return response
        except Exception as e:
            if attempt < max_retries:
                await asyncio.sleep(1)
            else:
                raise  # 3次全失败后抛出异常
```

---

## 13. 取消机制

```python
# 用户可设置取消事件
agent.cancel_event = asyncio.Event()

# 代理循环在每步开始和每个工具执行后检查
if self._check_cancelled():
    self._cleanup_incomplete_messages()
    return "Task cancelled by user."

# 取消后清理未完成的 assistant 消息
def _cleanup_incomplete_messages(self):
    # 移除最后一个 assistant 消息及其后续 tool 消息
    # 保证消息历史一致性
```

---

## 14. Token 管理

Mini-Agent 自动管理上下文长度：

- 当 `token > token_limit (默认 80000)` 时触发摘要
- `_summarize_messages()` 将执行过程压缩为摘要
- 保留所有 user 消息（用户意图）和 system 提示词
