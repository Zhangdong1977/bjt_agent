# bjt-agent Agent 规划与 Skill / Tool / MCP 说明

> 定位：面向用户的**简要技术说明**——先讲清系统的 agent 规划，再对 Tool、MCP、Skill 三种技术各给出**一个本系统的使用示例**。
> 需要完整架构细节（ReAct 循环机制、VSTO 协议、计费、可靠性工程）时，再读 [技术架构与Agent工程指导.md](技术架构与Agent工程指导.md)。

---

## 1. Agent 总体规划

### 1.1 规划原则

1. **一个基座，多种形态**：需要"LLM 自主多步决策"的场景继承 git 子模块 `Mini-Agent` 的 `Agent` 基类（ReAct 工具循环 + 上下文摘要 + SSE 实时进度）；不需要自主决策的场景不进工具循环，由代码直接编排 LLM 调用。**确定性优先**——检索、统计、评级全部由确定性代码完成，LLM 只做它擅长的事（语义比对、裁决、撰写）。
2. **主从编排（审查主线）**：MasterAgent 扫描规则库拆成待办 → 每个待办由一个子代理（BidReviewAgent）并行执行 → 汇总统计。主代理本身不调 LLM。
3. **任务即入口**：所有 agent 由 Celery 异步任务驱动，前端只做提交与进度展示；队列按负载分为 `review`（审查/查重/暗标）、`parser`（文档解析）、`generation`（标书生成）三类。

### 1.2 分层拓扑

```
前端 / 开放 API                     SSE 实时进度事件
  │  提交任务
  ▼
Celery 异步任务（review / parser / generation 三队列）
  ▼
编排 Agent：MasterAgent(审查) · DuplicateMasterAgent(查重) · BidWizardWriteAgent(编标)
  ▼
执行 Agent：
  BidReviewAgent（审查子代理，ReAct 工具循环）
  BlindCheckAgent（暗标检查，ReAct + 远程 Word 工具）
  DuplicateCheckAgent（查重，固定流程 + 单次 LLM 裁决）
  BidDraftAgent（标书草稿，代码编排生成管线）
  report_agent / QualityEvaluator（报告与质检，纯 LLM，失败可降级）
  ▼
支撑层：工具(Tool) · MCP · LLM 工厂 · 经验自学习闭环
```

### 1.3 六种 Agent 形态

| 形态 | 代表 Agent | 工具循环 | LLM 用途 |
|---|---|---|---|
| 编排器 | MasterAgent | 无（自身不调 LLM） | — |
| ReAct 审查子代理 | BidReviewAgent | ✅ 有，步数预算控制 | 逐步决策调哪个工具、产出问题清单 |
| ReAct 远程工具型 | BlindCheckAgent | ✅ 有，分阶段时间预算 | 调度 word_* 工具、复核结果 |
| 固定流程型 | DuplicateCheckAgent | ❌ | 对确定性候选对做一次裁决 |
| 生成管线型 | BidDraftAgent / BidWizard | ❌ 代码编排 | 分析→大纲→逐节撰写 |
| 纯 LLM 型 | report_agent / QualityEvaluator | ❌ | 报告精简 / 质量评分，失败可降级 |

### 1.4 各 Agent 一句话职责

- **MasterAgent + SubAgentExecutor**：扫规则库、建待办、并行派发子代理、异常重试（指数退避）、汇总统计。
- **BidReviewAgent**：审查主力。一个子代理负责一份规则文档的全部检查项，通过工具检索招标/投标文件、读取章节、理解图片、逐项合规比对，最后写出审查结果。
- **BlindCheckAgent**：检查用户本机打开的 Word 活文档是否满足暗标格式要求（字体、行距、页眉页脚、身份线索等），工具经服务中转下发到用户电脑上的 VSTO 插件执行。
- **DuplicateMasterAgent / DuplicateCheckAgent**：两份投标文件的雷同度检查——确定性检索工具先找出相似候选对，LLM 只对证据做一次裁决（reasonable / suspicious / unknown）。
- **BidDraftAgent / BidWizardWriteAgent**：AI 编标——分析招标要素、生成大纲、逐节撰写标书正文；红线是严禁虚构资质业绩，缺料用"（请补充）"占位。
- **report_agent / QualityEvaluator**：审查完成后汇总生成总体报告（LLM 失败自动降级为规则聚合版）；对审查输出做 LLM-as-Judge 三维质量评分。
- **经验自学习管线**（`backend/experience/`）：审查结束后用 LLM 从执行轨迹中提炼经验技能，经聚类、成熟度评分后，注入下一次审查的系统提示词，形成越用越准的闭环。

---

## 2. Tool 示例：`compare_bid` 合规比对工具

**Tool 是什么**：注册进 agent 工具表的能力单元。LLM 在 ReAct 循环的每一步自主决定调用哪个工具、传什么参数；工具执行后把结果回填给 LLM 继续推理。每个工具带名称、描述（模型据此选路）和参数 schema，统一返回 `ToolResult{success, content, error, data}`。

本系统中 BidReviewAgent 挂载了标书检索、章节读取、图片 OCR、合规比对等十余个工具，此处以最具业务代表性的**合规比对工具**为例（`backend/agent/tools/comparator.py`）：

```python
class ComparatorTool(BaseTool):
    name = "compare_bid"
    description = "【合规比对工具】将一条招标要求与投标书内容进行精确合规对比…"
    parameters = {
        "requirement": "招标要求（必填）",
        "bid_content": "投标响应内容（必填）",
        "severity": "critical / major / minor，默认 major",
    }
    async def execute(self, **kwargs) -> ToolResult:
        # 工具内部再调一次 LLM 做语义比对，输出结构化 JSON：
        # {is_compliant, severity, explanation, suggestion, location_page, location_line}
```

使用方式：审查子代理先用检索工具找到投标文件中的对应内容，再调用 `compare_bid(requirement=…, bid_content=…)`，拿到"是否合规 / 严重度 / 修改建议 / 位置"的结构化结论，汇入最终问题清单。

---

## 3. MCP 示例：MiniMax MCP 提供 `understand_image`

**MCP 是什么**：Model Context Protocol——按标准协议从外部 server 动态挂载工具，与本地工具无差别进入 agent 工具表，换供应商只需改配置。

本系统唯一的 MCP 接入点是 `backend/mcp.json`（stdio 方式启动子进程，提供 `understand_image` 图片视觉理解与 `web_search` 两个工具）：

```json
{
  "mcpServers": {
    "minimax_coding": {
      "description": "MiniMax Coding Plan MCP - 提供 understand_image 和 web_search 工具",
      "type": "stdio",
      "command": "uvx",
      "args": ["minimax-coding-plan-mcp"],
      "env": {
        "MINIMAX_API_KEY": "${MINIMAX_API_KEY}",
        "MINIMAX_API_HOST": "https://api.minimaxi.com"
      },
      "connect_timeout": 60.0,
      "execute_timeout": 120.0
    }
  }
}
```

工作方式：审查子代理初始化时加载此配置，由 Mini-Agent 基座的 `load_mcp_tools_async()` 启动 `uvx minimax-coding-plan-mcp` 子进程、列出远端工具并包装成普通 Tool 注册进工具表。此后 LLM 即可像调本地工具一样调用 `understand_image(prompt="这张资质证书是否在有效期内", image_source="…/cert.jpg")`，识别资质证书、印章、扫描件等图片内容。

> 说明：`understand_image` 除 MCP 通道外，还可在配置中切换为百度 OCR / 火山引擎 Vision 两条本地实现通道（工具名不变，生产环境以此规避外部子进程依赖）。

---

## 4. Skill 示例：`bjt-bid-review` 对外技能包

**Skill 是什么**：一个含 `SKILL.md` 的文件夹（Agent Skills 开放标准，兼容 Claude Code / 腾讯 WorkBuddy 生态）。SKILL.md 的 frontmatter 描述"什么时候用我"，正文是给 AI 助手的操作手册。Skill 让**外部 AI 助手**把本平台当能力使用——助手负责对话与流程引导，检查任务在标捷通云端执行。

本系统的对外技能包位于 `skill/bjt-bid-review/`（v0.2.1，已上架腾讯 SkillHub）：

```
skill/bjt-bid-review/
├── SKILL.md            # 技能定义 + 执行手册（流程、异常对照、输出铁律）
├── scripts/bjt.py      # CLI：login / upload / review / duplicate / status / cancel
└── references/         # api.md 接口契约 · usage.md 命令细节 · billing.md 计费口径
```

frontmatter 示例（description 决定外部助手何时触发本技能）：

```yaml
---
name: bjt-bid-review
description: 标捷通·AI标书检查与查重。上传招标文件与投标文件，由标捷通云端 AI
  完成标书审查（废标风险筛查、合规问题分级清单）和标书查重……当用户问
  「检查一下标书」「两份标书像不像」时使用。需标捷通 API Key；任务云端
  异步执行（通常数十分钟），按点计费。
---
```

典型一次使用：用户对外部 AI 助手说"帮我审查这份投标文件"→ 助手据 description 匹配到本技能 → 按 SKILL.md 流程引导用户粘贴 API Key、上传文件、**确认计费口径**后提交任务 → 轮询进度 → 把"高风险 N · 待复核 M"的结论与报告链接总结给用户。全程命令由助手后台代跑，用户只需提供文件、说需求、粘贴 Key。

> 说明：系统内部另有一套运行时"经验技能"（`backend/experience/`，见 §1.4 经验自学习管线），供审查 agent 自我进化使用，与上述对外技能包是两个不同层面的机制。

---

## 5. 三种技术的关系小结

| 技术 | 回答的问题 | 本系统示例 | 作用对象 |
|---|---|---|---|
| **Tool** | agent 这一步具体做什么 | `compare_bid` 合规比对 | 系统内的 agent |
| **MCP** | 如何标准化接入外部工具服务 | MiniMax MCP → `understand_image` | 系统内的 agent |
| **Skill** | 外部 AI 助手如何使用本系统 | `bjt-bid-review` 技能包 | 系统外的 AI 助手 |

一句话：**Tool 是手脚，MCP 是标准插座，Skill 是对外说明书**——agent 靠工具完成检查动作，靠 MCP 接入外部能力，靠 Skill 让第三方 AI 平台把整个系统当作一项技能来调用。
