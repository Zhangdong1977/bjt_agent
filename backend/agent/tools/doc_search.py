"""文档搜索工具，用于查询招标书和应标书内容。"""

import re
from html import unescape
from pathlib import Path
from typing import Optional

from backend.agent.tools.base import ToolResult
from mini_agent.tools.base import Tool as BaseTool

# 大文档分块大小
DEFAULT_CHUNK_SIZE = 8000  # 字符数
MAX_LINES_PER_QUERY = 100

# _extract_summary 函数的常量
_MIN_LINE_LENGTH = 5
_MAX_LINE_TRUNCATE = 150
_MAX_LINES_PER_CATEGORY = 3
_FALLBACK_LINE_TRUNCATE = 100
_FALLBACK_LINE_COUNT = 5

def strip_html_tags(text: str) -> str:
    """去除HTML标签，保留纯文本"""
    if not text:
        return ""
    # 先unescape HTML实体
    text = unescape(text)
    # 去除<script>和<style>标签及其内容
    text = re.sub(r'<\s*(script|style)[^>]*>.*?<\s*/\1\s*>', '', text, flags=re.DOTALL|re.IGNORECASE)
    # 去除所有HTML标签
    text = re.sub(r'<[^>]+>', '', text)
    # 清理多余空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def smart_truncate(text: str, max_length: int = 400) -> str:
    """智能截断，优先保留完整句子"""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    # 优先在句号处截断，保留完整句子
    truncated = text[:max_length]
    # 按优先级查找分隔符：句号 > 逗号 > 英文句号/逗号
    for sep in ['。', '，', '. ', ', ']:
        idx = truncated.rfind(sep)
        if idx > max_length * 0.5:
            return truncated[:idx+1]
    return truncated + '...'


# 预编译的分类模式，用于性能优化
_CATEGORY_PATTERNS = {
    "技术": (re.compile(r"技术|Python|Vue|FastAPI|开发", re.IGNORECASE), "🛠️"),
    "工期": (re.compile(r"工期|时间|交付|完成", re.IGNORECASE), "⏱️"),
    "预算": (re.compile(r"预算|价格|万|元|费用|成本", re.IGNORECASE), "💰"),
    "资质": (re.compile(r"资质|证书|认证|ISO|CMMI", re.IGNORECASE), "📋"),
}


class DocSearchTool(BaseTool):
    """用于搜索和读取招标书/应标书内容的工具。"""

    def __init__(self, tender_doc_path: str, bid_doc_path: str, chunk_size: int = DEFAULT_CHUNK_SIZE):
        """初始化文档搜索工具。

        Args:
            tender_doc_path: 已解析的招标书HTML文件路径
            bid_doc_path: 已解析的应标书HTML文件路径
            chunk_size: 大文档每块的最大字符数
        """
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        self.chunk_size = chunk_size
        # 文档缓存，避免重复磁盘读取
        self._cache: dict[str, tuple[str, list[str]]] = {}
        super().__init__()

    def _load_document(self, doc_path: Path) -> tuple[str, list[str]]:
        """加载文档并返回 (完整内容, 行列表)。

        使用缓存避免重复磁盘读取。
        """
        cache_key = str(doc_path)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not doc_path.exists():
            raise FileNotFoundError(f"文档未找到: {doc_path}")

        content = doc_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        self._cache[cache_key] = (content, lines)
        return content, lines

    def _find_line_around(self, lines: list[str], keyword: str, target_line: int = None) -> tuple[int, str]:
        """查找关键词匹配的行号和内容。

        Returns (行号, 行内容)。
        """
        for i, line in enumerate(lines):
            if keyword.lower() in line.lower():
                return i + 1, line  # 从1开始编号
        return -1, ""

    def _extract_summary(self, content: str) -> str:
        """从文档内容中提取结构化摘要。

        返回带分类区块的人类可读摘要。
        """
        lines = content.split('\n')
        summary_parts = []

        # 使用预编译模式查找每个分类的匹配行
        categorized_lines = {cat: [] for cat in _CATEGORY_PATTERNS}

        for line in lines:
            line_stripped = strip_html_tags(line.strip())
            if not line_stripped or len(line_stripped) < _MIN_LINE_LENGTH:
                continue
            for cat_name, (pattern, _icon) in _CATEGORY_PATTERNS.items():
                if pattern.search(line_stripped):
                    categorized_lines[cat_name].append(line_stripped[:_MAX_LINE_TRUNCATE])
                    break

        # 构建摘要
        for cat_name, (pattern, icon) in _CATEGORY_PATTERNS.items():
            lines_for_cat = categorized_lines[cat_name][:(_MAX_LINES_PER_CATEGORY)]
            if lines_for_cat:
                summary_parts.append(f"\n{icon} {cat_name}要求")
                for l in lines_for_cat:
                    summary_parts.append(f"• {l}")

        if not summary_parts:
            # 备用方案：取前几行非空行
            summary_parts.append("\n📝 文档内容")
            for line in lines[:(_FALLBACK_LINE_COUNT)]:
                if line.strip():
                    summary_parts.append(f"• {strip_html_tags(line.strip())[:(_FALLBACK_LINE_TRUNCATE)]}")

        return "\n".join(summary_parts)

    def _extract_image_refs(self, content: str) -> list[dict]:
        """从内容中提取所有markdown图片引用。

        返回列表: {alt_text, path, full_match, line_number}。
        """
        image_refs = []
        lines = content.split("\n")
        for i, line in enumerate(lines):
            matches = re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', line)
            for match in matches:
                image_refs.append({
                    "alt_text": match.group(1),
                    "path": match.group(2),
                    "full_match": match.group(0),
                    "line_number": i + 1,  # 从1开始编号
                })
        return image_refs

    def _search_by_keyword(self, lines: list[str], query: str, max_results: int = MAX_LINES_PER_QUERY) -> list[dict]:
        """按关键词搜索文档行并返回带上下文的匹配结果。

        返回列表: {line_number, line_content, context, image_refs}。
        """
        results = []
        query_lower = query.lower()

        for i, line in enumerate(lines):
            if query_lower in line.lower():
                # 获取上下文（前后各一行）
                # 先去除HTML，再截断，避免在标签中间截断
                context_before = smart_truncate(strip_html_tags(lines[max(0, i - 1)].strip()), 150) if i > 0 else ""
                context_after = smart_truncate(strip_html_tags(lines[min(len(lines) - 1, i + 1)].strip()), 150) if i < len(lines) - 1 else ""

                # 从当前行提取图片引用
                image_refs = []
                img_matches = re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', line)
                for img_match in img_matches:
                    image_refs.append({
                        "alt_text": img_match.group(1),
                        "path": img_match.group(2),
                        "full_match": img_match.group(0),
                    })

                results.append({
                    "line_number": i + 1,  # 从1开始编号
                    "line_content": smart_truncate(strip_html_tags(line.strip()), 400),
                    "context_before": context_before,
                    "context_after": context_after,
                    "image_refs": image_refs,
                })

                if len(results) >= max_results:
                    break

        return results

    def _chunk_content(self, content: str, chunk_num: int = 0) -> str:
        """将大内容分块并返回指定块。

        如果内容小于chunk_size，返回完整内容。
        如果chunk_num超出范围，返回空字符串。
        """
        if len(content) <= self.chunk_size:
            return content

        # 在段落或句子边界处分块
        chunks = []
        start = 0
        while start < len(content):
            end = start + self.chunk_size
            if end < len(content):
                # 尝试在段落或句子边界处断开
                break_points = [
                    content.rfind("\n\n", start, end),
                    content.rfind("\n", start, end),
                    content.rfind(". ", start, end),
                ]
                for bp in break_points:
                    if bp > start and bp >= end - 200:  # 至少进入块200个字符
                        end = bp + 1
                        break
            chunk = content[start:end]
            chunks.append(chunk)
            start = end

        # 返回请求的块，超出范围则返回空
        if chunk_num < 0 or chunk_num >= len(chunks):
            return ""

        return chunks[chunk_num]

    @property
    def name(self) -> str:
        return "search_tender_doc"

    @property
    def description(self) -> str:
        return """搜索和读取招标书/应标书内容。输入应为JSON对象，包含：
- '文档类型': 'tender' 或 'bid' (必填)
- 'query': 可选关键词，用于过滤内容（返回匹配行及上下文）
- 'chunk': 大文档的分块编号（0开始，用于分页）
- 'full_content': 设为true返回完整文档内容（可能被截断）

返回匹配行及行号和周围上下文。"""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "文档类型": {
                    "type": "string",
                    "enum": ["tender", "bid"],
                    "description": "要搜索的文档类型",
                },
                "query": {
                    "type": "string",
                    "description": "用于过滤内容的可选查询字符串（关键词搜索）",
                },
                "chunk": {
                    "type": "integer",
                    "description": "大文档的分块编号（0开始，用于分页）",
                    "default": 0,
                },
                "full_content": {
                    "type": "boolean",
                    "description": "如果为true，返回完整文档内容（可能被截断）",
                    "default": False,
                },
            },
            "required": ["文档类型"],
        }

    async def execute(
        self,
        文档类型: str = None,
        query: str = None,
        chunk: int = 0,
        full_content: bool = False,
        # 兼容中英文参数名
        doc_type: str = None,
        **kwargs,
    ) -> ToolResult:
        """执行文档搜索。

        Args:
            文档类型: 'tender' 或 'bid' (来自LLM的中文参数名)
            doc_type: 'tender' 或 'bid' (英文参数名，备用)
            query: 可选的查询字符串，用于过滤行
            chunk: 大文档分页的分块编号
            full_content: 如果为True，返回完整文档（可能被截断）

        Returns:
            包含文档内容和元数据的ToolResult
        """
        # 从中文或英文参数名解析doc_type
        resolved_doc_type = 文档类型 or doc_type
        if not resolved_doc_type:
            return ToolResult(success=False, content="", error="缺少必需参数: 文档类型 或 doc_type")

        try:
            doc_path = Path(self.tender_doc_path if resolved_doc_type == "tender" else self.bid_doc_path)
            _, lines = self._load_document(doc_path)

            # 如果请求完整内容，返回友好摘要
            if full_content:
                full_text = "\n".join(lines)
                total_lines = len(lines)
                total_chunks = (len(full_text) + self.chunk_size - 1) // self.chunk_size if len(full_text) > self.chunk_size * 3 else 1

                if len(full_text) > self.chunk_size * 3:
                    full_text = self._chunk_content(full_text, chunk)
                    if chunk > 0:
                        full_text = f"[... 第 {chunk} 块 ...]\n{full_text}"
                        current_chunk_lines = len(full_text.split('\n'))
                    else:
                        current_chunk_lines = len(full_text.split('\n'))
                else:
                    current_chunk_lines = total_lines

                # 生成友好摘要
                summary = self._extract_summary(full_text)
                doc_label = "招标" if doc_type == "tender" else "投标"

                # 分页提示
                if chunk > 0:
                    pagination_note = f"\n📄 当前第 {chunk + 1} 块，共 {total_chunks} 块"
                else:
                    pagination_note = ""

                friendly_content = f"""📄 {doc_label}书内容摘要

这份{doc_label}书共 {total_lines} 行，内容如下：

{summary}{pagination_note}

[完整文档已加载]"""

                # 如果也提供了query，包含关键词匹配摘要
                query_match_info = ""
                keyword_matches = []
                if query:
                    keyword_matches = self._search_by_keyword(lines, query)
                    if keyword_matches:
                        query_match_info = f"\n\n📌 关键词\"{query}\"匹配情况：找到 **{len(keyword_matches)}** 处"
                        # 显示前3个匹配作为示例
                        for i, m in enumerate(keyword_matches[:3], 1):
                            query_match_info += f"\n   {i}. {m['line_content'][:80]}..."
                    else:
                        query_match_info = f"\n\n📌 关键词\"{query}\"未在正文匹配（可能在分类标题中）"
                    # 将query信息追加到内容
                    friendly_content += query_match_info

                # 从完整文档提取所有图片引用
                all_image_refs = self._extract_image_refs(full_text)
                has_images = len(all_image_refs) > 0
                if has_images:
                    friendly_content += f"\n\n📷 文档包含 **{len(all_image_refs)}** 张图片"

                return ToolResult(
                    success=True,
                    content=friendly_content,
                    data={
                        "line_count": total_lines,
                        "chunk": chunk,
                        "total_chunks": total_chunks,
                        "current_chunk_lines": current_chunk_lines,
                        "full_content": full_text,
                        "query_matches": len(keyword_matches),
                        "has_images": has_images,
                        "image_refs": all_image_refs,
                    },
                )

            # 如果提供了query，按关键词搜索
            if query:
                matches = self._search_by_keyword(lines, query)

                if not matches:
                    doc_label = "招标" if doc_type == "tender" else "投标"
                    # 检查query是否可能在分类标题中
                    query_lower = query.lower()
                    header_matches = []
                    category_indicators = ["技术", "工期", "预算", "资质", "要求", "规格", "标准", "条件"]
                    for i, line in enumerate(lines):
                        line_stripped = strip_html_tags(line.strip()).lower()
                        if query_lower in line_stripped:
                            # 检查这是否像标题（包含分类标识符）
                            if any(cat in line_stripped for cat in category_indicators):
                                header_matches.append(strip_html_tags(line.strip())[:100])
                            if len(header_matches) >= 3:
                                break

                    if header_matches:
                        hint_lines = "\n".join(f"   • {h}" for h in header_matches[:3])
                        return ToolResult(
                            success=True,
                            content=f"""抱歉，未在{doc_label}书中找到与"{query}"直接匹配的内容。

💡 可能匹配的场景：
{hint_lines}

这表明"{query}"可能出现在分类标题中，而非正文内容。建议使用 full_content=true 获取完整文档后人工查阅相关章节。""",
                            data={"query": query, "matches": 0, "search_mode": "keyword", "header_matches": header_matches},
                        )
                    else:
                        return ToolResult(
                            success=True,
                            content=f"抱歉，未在{doc_label}书中找到与\"{query}\"相关的内容。",
                            data={"query": query, "matches": 0, "search_mode": "keyword"},
                        )

                # 格式化结果，包含上下文和引用
                doc_label = "招标" if doc_type == "tender" else "投标"
                display_matches = matches[:10]
                formatted = [f"🔍 在{doc_label}书中找到 **{len(matches)}** 处提到\"{query}\"：\n"]
                citations = []
                has_images = False
                all_image_refs = []
                for i, m in enumerate(display_matches, 1):
                    # 格式化引用，包含行号和引用内容
                    formatted.append(f"{i}. {m['line_content']}")
                    if m.get('context_before'):
                        formatted.append(f"   ↳ 上文: {m['context_before']}")
                    if m.get('context_after'):
                        formatted.append(f"   ↳ 下文: {m['context_after']}")
                    # 检查此匹配中的图片引用
                    img_refs = m.get('image_refs', [])
                    if img_refs:
                        has_images = True
                        for img in img_refs:
                            img["line_number"] = m['line_number']
                            all_image_refs.append(img)
                        formatted.append(f"   📷 图片: {img_refs[0]['path']}")
                        if len(img_refs) > 1:
                            formatted.append(f"   (还有 {len(img_refs) - 1} 张图片)")
                    # 构建结构化引用数据
                    citations.append({
                        "index": i,
                        "line_number": m['line_number'],
                        "quote": m['line_content'],
                        "context_before": m.get('context_before', ''),
                        "context_after": m.get('context_after', ''),
                        "image_refs": img_refs,
                    })

                if len(matches) > 10:
                    formatted.append(f"\n... 还有 {len(matches) - 10} 处匹配")

                return ToolResult(
                    success=True,
                    content="\n".join(formatted),
                    data={
                        "query": query,
                        "matches": len(matches),
                        "results": matches,
                        "citations": citations,
                        "has_images": has_images,
                        "image_refs": all_image_refs,
                    },
                )

            # 无query时，返回文档信息
            doc_label = "招标" if doc_type == "tender" else "投标"
            return ToolResult(
                success=True,
                content=f"📄 已加载{doc_label}书，共 {len(lines)} 行。",
                data={
                    "line_count": len(lines),
                    "doc_type": doc_type,
                    "path": str(doc_path),
                },
            )

        except FileNotFoundError as e:
            return ToolResult(
                success=False,
                content="",
                error=f"文档未找到: {e}",
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))

    def clear_cache(self) -> None:
        """清除文档缓存。"""
        self._cache.clear()
