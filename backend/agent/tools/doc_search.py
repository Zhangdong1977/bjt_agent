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
    """用于搜索和读取招标书/应标书内容的工具。

    支持每种文档类型（招标/投标）的多个文件。LLM 可通过 doc_name 参数
    指定搜索某个特定文件，不指定则搜索该类型所有文件。
    """

    def __init__(
        self,
        tender_docs: list[tuple[str, str]],
        bid_docs: list[tuple[str, str]],
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        """初始化文档搜索工具。

        Args:
            tender_docs: 招标文件列表 [(文件名, 解析后路径), ...]
            bid_docs: 投标文件列表 [(文件名, 解析后路径), ...]
            chunk_size: 大文档每块的最大字符数
        """
        # 存储多文档映射 {doc_type: {doc_name: path}}
        self._docs: dict[str, dict[str, str]] = {
            "tender": {name: path for name, path in tender_docs},
            "bid": {name: path for name, path in bid_docs},
        }
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

    def _load_all_documents(self, doc_type: str) -> list[tuple[str, str, list[str]]]:
        """加载某类型所有文档，返回 [(doc_name, content, lines), ...]。"""
        result = []
        for name, path in self._docs[doc_type].items():
            _, lines = self._load_document(Path(path))
            result.append((name, "\n".join(lines), lines))
        return result

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

    # 默认上下文窗口大小（匹配行前后各N行）
    _DEFAULT_CONTEXT_WINDOW = 5

    def _search_by_keyword(self, lines: list[str], query: str, max_results: int = MAX_LINES_PER_QUERY, context_lines: int = _DEFAULT_CONTEXT_WINDOW) -> list[dict]:
        """按关键词搜索文档行并返回带上下文的匹配结果。

        Args:
            context_lines: 匹配行前后各返回的上下文行数

        返回列表: {line_number, line_content, context_before, context_after, image_refs}。
        """
        results = []
        query_lower = query.lower()

        for i, line in enumerate(lines):
            if query_lower in line.lower():
                # 获取上下文（前后各 context_lines 行）
                # 先去除HTML，再截断，避免在标签中间截断
                before_start = max(0, i - context_lines)
                before_lines = [
                    smart_truncate(strip_html_tags(lines[j].strip()), 200)
                    for j in range(before_start, i)
                ]
                context_before = "\n".join(before_lines)

                after_end = min(len(lines), i + context_lines + 1)
                after_lines = [
                    smart_truncate(strip_html_tags(lines[j].strip()), 200)
                    for j in range(i + 1, after_end)
                ]
                context_after = "\n".join(after_lines)

                # 从当前行提取图片引用
                image_refs = []
                img_matches = re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', line)
                for img_match in img_matches:
                    image_refs.append({
                        "alt_text": img_match.group(1),
                        "path": img_match.group(2),
                        "full_match": img_match.group(0),
                    })

                # 如果当前行无图片引用，扫描邻近行（±3行）寻找图片
                if not image_refs:
                    _NEARBY_WINDOW = 3
                    for offset in range(-_NEARBY_WINDOW, _NEARBY_WINDOW + 1):
                        if offset == 0:
                            continue
                        nearby_idx = i + offset
                        if nearby_idx < 0 or nearby_idx >= len(lines):
                            continue
                        for img_match in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', lines[nearby_idx]):
                            image_refs.append({
                                "alt_text": img_match.group(1),
                                "path": img_match.group(2),
                                "full_match": img_match.group(0),
                                "nearby_line_number": nearby_idx + 1,
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
        # 动态构建可用文档列表
        doc_list_parts = []
        for doc_type, docs in self._docs.items():
            label = "招标文件" if doc_type == "tender" else "投标文件"
            names = "、".join(f"\"{n}\"" for n in docs.keys())
            doc_list_parts.append(f"{label}({len(docs)}份): {names}")
        doc_list_str = "；".join(doc_list_parts)

        return f"""【招标/投标文档专用搜索工具】查询招标书(tender)或投标书(bid)中的内容。

这是查询招标书和投标书的唯一正确工具。禁止使用 read_file 读取招标书或投标书。

当前项目可用文档：{doc_list_str}

参数说明（JSON对象）：
- "文档类型": "tender"（查招标书）或 "bid"（查投标书），必填
- "doc_name": 可选，指定搜索某个特定文件（如"补充通知.docx"）。不指定则搜索该类型所有文件
- "query": 搜索关键词，必填。返回所有包含该关键词的行及上下文
- "chunk": 分页编号（从0开始），大文档分页时使用
- "full_content": 设为true返回完整文档（仅在无query时使用）
- "context_lines": 匹配行前后各返回的上下文行数，默认5。精确引用用1-3，理解语义用5-10

返回：匹配行的行号、内容和上下文，多文件搜索时每条结果会标注来源文件名。"""

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
                "doc_name": {
                    "type": "string",
                    "description": "可选，指定搜索某个特定文件（如文件名'补充通知.docx'）。不指定则搜索该类型所有文件",
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
                "context_lines": {
                    "type": "integer",
                    "description": "关键词搜索时，匹配行前后各返回的上下文行数。默认5行。需要精确引用时可设小（如1-3），需要理解上下文语义时可设大（如5-10）",
                    "default": 5,
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
        context_lines: int = 5,
        doc_name: str = None,
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
            context_lines: 关键词搜索时匹配行前后各返回的上下文行数
            doc_name: 可选，指定搜索某个特定文件。不指定则搜索该类型所有文件

        Returns:
            包含文档内容和元数据的ToolResult
        """
        # 从中文或英文参数名解析doc_type
        resolved_doc_type = 文档类型 or doc_type
        if not resolved_doc_type:
            return ToolResult(success=False, content="", error="缺少必需参数: 文档类型 或 doc_type")

        if resolved_doc_type not in self._docs:
            return ToolResult(success=False, content="", error=f"未知文档类型: {resolved_doc_type}")

        doc_map = self._docs[resolved_doc_type]
        if not doc_map:
            doc_label = "招标" if resolved_doc_type == "tender" else "投标"
            return ToolResult(success=False, content="", error=f"没有可用的{doc_label}文件")

        # 确定要搜索的目标文档
        if doc_name:
            if doc_name not in doc_map:
                available = "、".join(f"\"{n}\"" for n in doc_map.keys())
                return ToolResult(
                    success=False,
                    content="",
                    error=f"未找到文档 \"{doc_name}\"。可用文档：{available}",
                )
            targets = {doc_name: doc_map[doc_name]}
        else:
            targets = doc_map

        try:
            # === full_content 模式 ===
            if full_content:
                return self._execute_full_content(
                    resolved_doc_type, targets, query, chunk, doc_name
                )

            # === 关键词搜索模式 ===
            if query:
                return self._execute_keyword_search(
                    resolved_doc_type, targets, query, context_lines, doc_name
                )

            # === 无 query，返回文档信息 ===
            doc_label = "招标" if resolved_doc_type == "tender" else "投标"
            info_parts = []
            for name, path in targets.items():
                _, lines = self._load_document(Path(path))
                info_parts.append(f"📄 {name}: {len(lines)} 行")
            return ToolResult(
                success=True,
                content=f"{doc_label}书已加载：\n" + "\n".join(info_parts),
                data={
                    "doc_type": resolved_doc_type,
                    "documents": [
                        {"name": name, "line_count": len(self._load_document(Path(path))[1])}
                        for name, path in targets.items()
                    ],
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

    def _execute_full_content(
        self,
        resolved_doc_type: str,
        targets: dict[str, str],
        query: str | None,
        chunk: int,
        doc_name: str | None,
    ) -> ToolResult:
        """处理 full_content 模式的搜索。"""
        doc_label = "招标" if resolved_doc_type == "tender" else "投标"
        is_multi = len(targets) > 1

        all_lines: list[str] = []
        # 记录每个文档在合并 lines 中的偏移量，用于标注来源
        doc_offsets: list[tuple[str, int, int]] = []  # (name, start, end)

        for name, path in targets.items():
            _, lines = self._load_document(Path(path))
            start_offset = len(all_lines)
            if is_multi:
                all_lines.append(f"========== 文档: {name} ==========")
            all_lines.extend(lines)
            doc_offsets.append((name, start_offset, len(all_lines)))

        full_text = "\n".join(all_lines)
        total_lines = len(all_lines)
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

        # 分页提示
        if chunk > 0:
            pagination_note = f"\n📄 当前第 {chunk + 1} 块，共 {total_chunks} 块"
        else:
            pagination_note = ""

        file_count_note = f"（共 {len(targets)} 份文件）" if is_multi else ""
        friendly_content = f"""📄 {doc_label}书内容摘要{file_count_note}

这份{doc_label}书共 {total_lines} 行，内容如下：

{summary}{pagination_note}

[完整文档已加载]"""

        # 如果也提供了query，包含关键词匹配摘要
        keyword_matches: list[dict] = []
        if query:
            keyword_matches = self._search_by_keyword(all_lines, query)
            if keyword_matches:
                friendly_content += f"\n\n📌 关键词\"{query}\"匹配情况：找到 **{len(keyword_matches)}** 处"
                for i, m in enumerate(keyword_matches[:3], 1):
                    friendly_content += f"\n   {i}. {m['line_content'][:80]}..."
            else:
                friendly_content += f"\n\n📌 关键词\"{query}\"未在正文匹配（可能在分类标题中）"

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

    def _execute_keyword_search(
        self,
        resolved_doc_type: str,
        targets: dict[str, str],
        query: str,
        context_lines: int,
        doc_name: str | None,
    ) -> ToolResult:
        """处理关键词搜索模式。"""
        doc_label = "招标" if resolved_doc_type == "tender" else "投标"
        is_multi = len(targets) > 1

        # 收集所有文档的搜索结果
        all_matches: list[dict] = []
        for name, path in targets.items():
            _, lines = self._load_document(Path(path))
            matches = self._search_by_keyword(lines, query, context_lines=context_lines)
            for m in matches:
                m["source_doc"] = name
                if is_multi:
                    # 多文件时，在 line_content 前标注来源
                    m["display_prefix"] = f"[{name}] "
                else:
                    m["display_prefix"] = ""
            all_matches.extend(matches)

        if not all_matches:
            # 检查query是否可能在分类标题中
            query_lower = query.lower()
            header_matches = []
            category_indicators = ["技术", "工期", "预算", "资质", "要求", "规格", "标准", "条件"]
            for name, path in targets.items():
                _, lines = self._load_document(Path(path))
                for line in lines:
                    line_stripped = strip_html_tags(line.strip()).lower()
                    if query_lower in line_stripped:
                        if any(cat in line_stripped for cat in category_indicators):
                            header_matches.append(strip_html_tags(line.strip())[:100])
                        if len(header_matches) >= 3:
                            break
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

        # 格式化结果
        display_matches = all_matches[:10]
        formatted = [f"🔍 在{doc_label}书中找到 **{len(all_matches)}** 处提到\"{query}\"：\n"]
        citations = []
        has_images = False
        all_image_refs = []

        for i, m in enumerate(display_matches, 1):
            prefix = m.get("display_prefix", "")
            formatted.append(f"{i}. {prefix}{m['line_content']}")
            if m.get('context_before'):
                before_lines = m['context_before'].split('\n')
                formatted.append(f"   ↳ 上文({len(before_lines)}行):")
                for bl in before_lines:
                    if bl:
                        formatted.append(f"      {bl}")
            if m.get('context_after'):
                after_lines = m['context_after'].split('\n')
                formatted.append(f"   ↳ 下文({len(after_lines)}行):")
                for al in after_lines:
                    if al:
                        formatted.append(f"      {al}")
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
            citation = {
                "index": i,
                "line_number": m['line_number'],
                "quote": m['line_content'],
                "context_before": m.get('context_before', ''),
                "context_after": m.get('context_after', ''),
                "image_refs": img_refs,
            }
            if "source_doc" in m:
                citation["source_doc"] = m["source_doc"]
            citations.append(citation)

        if len(all_matches) > 10:
            formatted.append(f"\n... 还有 {len(all_matches) - 10} 处匹配")

        return ToolResult(
            success=True,
            content="\n".join(formatted),
            data={
                "query": query,
                "matches": len(all_matches),
                "results": all_matches,
                "citations": citations,
                "has_images": has_images,
                "image_refs": all_image_refs,
            },
        )

    def clear_cache(self) -> None:
        """清除文档缓存。"""
        self._cache.clear()
