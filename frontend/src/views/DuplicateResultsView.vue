<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

import { duplicateApi, documentsApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import type {
  DocumentContent,
  DuplicateEvidenceCluster,
  DuplicateMatrixResponse,
  DuplicateResult,
  DuplicateResultsResponse,
  DuplicateTableComparison,
  DuplicateVerdict,
} from '@/types'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.id as string)
const selectedTaskId = ref('')
const loading = ref(false)
const data = ref<DuplicateResultsResponse | null>(null)
const tableComparisons = ref<DuplicateTableComparison[]>([])
const tableWarnings = ref<string[]>([])
const matrix = ref<DuplicateMatrixResponse | null>(null)
const clusters = ref<DuplicateEvidenceCluster[]>([])
// 原始文档缓存：documentId → DocumentContent，避免切换重复点时反复拉取全文。
const documentCache = ref<Record<string, DocumentContent>>({})
// 原文加载失败记录：documentId → true，用于显示「加载失败」而非一直 spin。
const documentErrors = ref<Record<string, boolean>>({})
const docLoading = ref(false)
// A/B 原文容器 DOM 引用，用于切换重复点后把高亮段滚动到视口中央。
const leftDocRef = ref<HTMLElement | null>(null)
const rightDocRef = ref<HTMLElement | null>(null)

const leftDocument = computed(() =>
  projectStore.documents.find((document) => document.doc_type === 'duplicate_left')
)
const rightDocument = computed(() =>
  projectStore.documents.find((document) => document.doc_type === 'duplicate_right')
)
const batchDocuments = computed(() =>
  projectStore.documents
    .filter((document) => document.doc_type === 'duplicate_bid')
    .sort((a, b) => (a.duplicate_ordinal ?? 999) - (b.duplicate_ordinal ?? 999)),
)

// 重复点按「A 文档位置顺序」排列：先按 A 文档文件名（batch 多文档分桶），
// 再按 section 文本（中文本地化排序），最后按 start_line / end_line。
// 位置字段可能缺失，start_line 用 Infinity 兜底（与后端 grouper 同思路）。
const sortedFindings = computed(() => {
  const findings = data.value?.findings || []
  return [...findings].sort((a, b) => {
    const fileCmp = String(a.left_filename ?? '').localeCompare(String(b.left_filename ?? ''), 'zh-Hans-CN')
    if (fileCmp !== 0) return fileCmp
    const secCmp = String(a.left_location?.section ?? '').localeCompare(
      String(b.left_location?.section ?? ''),
      'zh-Hans-CN',
    )
    if (secCmp !== 0) return secCmp
    const aStart = Number(a.left_location?.start_line) || Infinity
    const bStart = Number(b.left_location?.start_line) || Infinity
    if (aStart !== bStart) return aStart - bStart
    const aEnd = Number(a.left_location?.end_line) || Infinity
    const bEnd = Number(b.left_location?.end_line) || Infinity
    return aEnd - bEnd
  })
})

// 选中「一处证据组」。详情/高亮全部基于该组的 representative。
const selectedGroupKey = ref('')
const selectedGroup = computed<EvidenceGroup | null>(() =>
  groupedFindings.value.find((group) => group.key === selectedGroupKey.value) || null,
)
// selectedGroup 的 representative，供详情/高亮直接读取（字段结构与单条 finding 相同）。
const selectedFinding = computed(() => selectedGroup.value?.representative ?? null)

onMounted(async () => {
  await projectStore.selectProject(projectId.value)
  await projectStore.fetchDuplicateTasks()
  const requested = route.query.taskId as string | undefined
  const selected = requested && projectStore.reviewTasks.some((task) => task.id === requested)
    ? requested
    : projectStore.reviewTasks[0]?.id
  if (selected) selectedTaskId.value = selected
})

watch(selectedTaskId, async (taskId) => {
  if (!taskId) return
  if (route.query.taskId !== taskId) {
    await router.replace({ query: { ...route.query, taskId } })
  }
  loading.value = true
  // 切换任务时清空原文缓存（不同任务的文档集合可能不同）。
  documentCache.value = {}
  documentErrors.value = {}
  try {
    data.value = await duplicateApi.getResults(projectId.value, taskId)
    try {
      matrix.value = await duplicateApi.getMatrix(projectId.value, taskId)
      clusters.value = await duplicateApi.getClusters(projectId.value, taskId, true)
    } catch {
      matrix.value = null
      clusters.value = []
    }
    try {
      const tables = await duplicateApi.getTableComparisons(projectId.value, taskId)
      tableComparisons.value = tables.comparisons || []
      tableWarnings.value = tables.warnings || []
    } catch {
      tableComparisons.value = []
      tableWarnings.value = ['表格结构对照暂不可用']
    }
    // 默认选中第一处证据组；若上次选中的组仍在新结果中则保留。
    const groups = groupedFindings.value
    const stillSelected = groups.some((group) => group.key === selectedGroupKey.value)
    selectedGroupKey.value = stillSelected ? selectedGroupKey.value : (groups[0]?.key ?? '')
    // 结果加载后立即预取本任务涉及的全部原始文档（去重后串行加载，避免并发触发
    // token 刷新竞态导致某些文档请求挂起）。每个文档设 12s 超时兜底。
    void prefetchTaskDocuments()
  } catch {
    data.value = null
    tableComparisons.value = []
    message.error('加载查重结果失败')
  } finally {
    loading.value = false
  }
}, { immediate: true })

function verdictLabel(verdict: string): string {
  const labels: Record<string, string> = {
    reasonable: '合理重复',
    suspicious: '疑似不合理重复',
    unknown: '证据不足 / 来源待确认',
  }
  return labels[verdict] || '证据不足 / 来源待确认'
}

function sourceBasisLabel(sourceBasis: string): string {
  const labels: Record<string, string> = {
    tender: '招标文件证据',
    public: '公开来源证据',
    bidder_authored: '投标文件原文证据',
    unknown: '来源待确认',
  }
  return labels[sourceBasis] || '来源待确认'
}

function evidenceStrength(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : '待评估'
}

function matchTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    exact: '完全一致',
    near_exact: '近似复制',
    semantic: '语义相似',
    structural: '结构/数字一致',
    ocr_error: '相同 OCR 异常',
    logic_anomaly: '归属逻辑异常',
  }
  return labels[type] || type
}

function locationText(location: Record<string, any>): string {
  const parts = [location?.section]
  if (location?.page_number || location?.page) parts.push(`第 ${location.page_number || location.page} 页`)
  if (location?.start_line) {
    parts.push(`第 ${location.start_line}${location.end_line && location.end_line !== location.start_line ? `-${location.end_line}` : ''} 行`)
  }
  return parts.filter(Boolean).join(' · ') || '位置未知'
}

// —— 原始文档高亮：把重复点 excerpt 在整篇渲染后的文档里定位并用 <mark> 包裹 ——
// 后端 location 只到 start_line/end_line + section（实测多为空 {}），无字符偏移；
// excerpt 是 verbatim 重复文本。两大难点：
//   ① excerpt 是【原始 Markdown 源码】（带 ** ## | 等），渲染后的文档已把标记转成
//      HTML 标签，且 marked 会吃掉反斜杠转义（C:\Bid 渲染成 C:Bid）。因此匹配用的
//      归一化必须把两侧的 Markdown/格式噪声一并剥除，才能收敛到同一规范串。
//   ② grouper 合并后的长 excerpt 会跨多个段落/标签节点，需「连续文本流」跨节点匹配。

// 用于「比较」的归一化：剥掉所有 Markdown/格式噪声与空白，只留「文字 + 字母数字 +
// 少量标点」，全角转半角、转小写。两侧（excerpt 与渲染后 DOM 文本）走同一归一化后，
// `**x**`→x、`## `→(无)、`|`→(无)、`\`→(无)、`C:\Bid` 与 `C:Bid` 都收敛为 `c:bid`。
const NOISE_CHARS_RE = /[`*_~#|>\\{}\[\]()!<>=\-+.]/g
function normalizeText(s: string): string {
  return s
    .replace(/\s+/g, '')            // 去所有空白（含换行、全角空格）
    .replace(NOISE_CHARS_RE, '')    // 去 Markdown/格式噪声字符
    .replace(/["""'']/g, '')        // 去各类引号（中文/英文/智能引号）
    .replace(/[，,。.；;：:、]/g, '') // 去中英文逗号/句号/分号/冒号/顿号
    .toLowerCase()
}

// 把 Markdown 源码还原成纯文本（用于按句切锚点，非用于逐字比较）。
function stripMarkdown(s: string): string {
  return s
    // 代码块/行内代码 → 内容
    .replace(/```[\s\S]*?```/g, (m) => m.replace(/```/g, '').replace(/^\w*\n?/, ''))
    .replace(/`([^`]+)`/g, '$1')
    // 图片 ![alt](url) → alt
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    // 链接 [text](url) → text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    // 标题前的 # 号
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    // 加粗/斜体标记 ** __ * _
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1$2')
    .replace(/(^|[^_])_([^_]+)_(?!_)/g, '$1$2')
    // 删除线 ~~text~~
    .replace(/~~([^~]+)~~/g, '$1')
    // 引用 > 、列表标记 - * + 1.（行首）
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s{0,3}[-*+]\s+/gm, '')
    .replace(/^\s{0,3}\d+\.\s+/gm, '')
    // 表格分隔/边框竖线
    .replace(/^\s*\|?\s*[-:]+\s*\|[-|\s:]+$/gm, '')
    .replace(/\s*\|\s*/g, ' ')
    .replace(/^\||\|$/gm, '')
    // 水平分隔线
    .replace(/^\s{0,3}([-*_])\1{2,}\s*$/gm, '')
    // HTML 注释与残留标签
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<[^>]+>/g, '')
    .trim()
}

// 收集 doc 内所有文本节点，返回 { nodes, fullNorm }：fullNorm 是拼接后的归一化字符串。
function collectText(doc: Document): { nodes: Text[]; fullNorm: string; parts: string[] } {
  const root = doc.getElementById('__root') || doc.body
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  let n: Node | null
  while ((n = walker.nextNode())) nodes.push(n as Text)
  const parts = nodes.map((tn) => normalizeText(tn.nodeValue || ''))
  return { nodes, parts, fullNorm: parts.join('') }
}

// 把归一化字符串里的 [start, end) 区间映射回 DOM，用 <mark class="dup-hit"> 包裹。
// 跨节点时用 Range.extractContents 包裹。返回是否成功包裹。
function markRange(doc: Document, ctx: ReturnType<typeof collectText>, start: number, end: number): boolean {
  const { nodes, parts } = ctx
  let acc = 0, startNode = -1, startOff = -1, endNode = -1, endOff = -1
  for (let i = 0; i < parts.length; i++) {
    const nodeStart = acc
    const nodeEnd = acc + parts[i].length
    if (startNode < 0 && start < nodeEnd) { startNode = i; startOff = start - nodeStart }
    if (end <= nodeEnd) { endNode = i; endOff = end - nodeStart; break }
    acc = nodeEnd
  }
  if (startNode < 0 || endNode < 0) return false
  // 归一化下标 → 原始 nodeValue 下标（逐字符对齐累计）
  const toOrig = (textNode: Text, normOffset: number): number => {
    const full = textNode.nodeValue || ''
    let oi = 0, ni = 0
    while (oi < full.length && ni < normOffset) {
      if (normalizeText(full[oi]) !== '') ni++
      oi++
    }
    return oi
  }
  const range = doc.createRange()
  range.setStart(nodes[startNode], toOrig(nodes[startNode], startOff))
  range.setEnd(nodes[endNode], toOrig(nodes[endNode], endOff))
  const mark = doc.createElement('mark')
  mark.className = 'dup-hit'
  try {
    range.surroundContents(mark)
  } catch {
    const frag = range.extractContents()
    mark.appendChild(frag)
    range.insertNode(mark)
  }
  return true
}

// 按归一化子串 needle 标记首个命中（整串必须在文档中出现）。
function markFirstInDoc(doc: Document, ctx: ReturnType<typeof collectText>, needle: string): boolean {
  if (needle.length < 6) return false
  const idx = ctx.fullNorm.indexOf(needle)
  if (idx < 0) return false
  return markRange(doc, ctx, idx, idx + needle.length)
}

// 从 excerpt 里取若干个用于匹配的候选片段（长句锚点优先）。
function excerptAnchors(excerpt: string): string[] {
  const clean = stripMarkdown(excerpt)
  if (!clean) return []
  const frags = clean
    .split(/[。\n；;！!？?]/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 12)
    .sort((a, b) => b.length - a.length)
  return frags.length ? frags : (clean.length >= 6 ? [clean] : [])
}

// 滑动窗口兜底：在归一化 excerpt 与归一化文档之间找公共子串并高亮。
// 【关键】按「中文字符数」择优，而非单纯最长——否则会优先命中长路径/数字串
// （如 C:\Bid\Aurora\release\final_v17.docx 归一化后是 29 字纯 ASCII），而真正
// 的重复证据是较短的中文名（如「北辰科技有限公司」）。因此 DP 同时累计长度与
// 中文字符数，优先取中文字符最多者，相同时取更长；阈值要求至少 4 个中文字符，
// 或（无中文候选时）长度 ≥8 的纯字母数字串。
function markLongestWindow(doc: Document, ctx: ReturnType<typeof collectText>, excerpt: string): boolean {
  const a = normalizeText(stripMarkdown(excerpt))
  if (a.length < 8) return false
  const b = ctx.fullNorm
  if (!b) return false
  const isCJK = (ch: string) => ch >= '\u4e00' && ch <= '\u9fff'
  // DP：prevLen/prevCjk 与 curLen/curCjk 记录以 (i,j) 结尾的公共子串长度与其中文字符数
  const prevLen = new Int16Array(a.length + 1)
  const prevCjk = new Int16Array(a.length + 1)
  const curLen = new Int16Array(a.length + 1)
  const curCjk = new Int16Array(a.length + 1)
  let bestLen = 0, bestCjk = 0, bestAEnd = 0
  for (let j = 1; j <= b.length; j++) {
    for (let i = 1; i <= a.length; i++) {
      if (a[i - 1] === b[j - 1]) {
        curLen[i] = (prevLen[i - 1] + 1) as number
        curCjk[i] = (prevCjk[i - 1] + (isCJK(a[i - 1]) ? 1 : 0)) as number
        // 择优：中文字符数优先；相同时取更长
        if (curCjk[i] > bestCjk || (curCjk[i] === bestCjk && curLen[i] > bestLen)) {
          bestCjk = curCjk[i]; bestLen = curLen[i]; bestAEnd = i
        }
      } else {
        curLen[i] = 0; curCjk[i] = 0
      }
    }
    prevLen.set(curLen); prevCjk.set(curCjk)
    curLen.fill(0); curCjk.fill(0)
  }
  // 阈值：至少 4 个中文字符；若无中文候选则退化为长度 ≥8（纯数字/编号证据）
  if (bestCjk < 4 && bestLen < 8) return false
  const aStart = bestAEnd - bestLen
  const needle = a.slice(aStart, bestAEnd)
  const idx = b.indexOf(needle)
  if (idx < 0) return false
  return markRange(doc, ctx, idx, idx + needle.length)
}

// 多级匹配：长句锚点 → 全文 excerpt → 最长公共子串（滑动窗口）→ 不高亮。
// 返回处理后的 HTML 与是否命中。
function highlightExcerpt(safeHtml: string, excerpt: string): { html: string; hit: boolean } {
  if (!excerpt || !excerpt.trim() || typeof DOMParser === 'undefined') {
    return { html: safeHtml, hit: false }
  }
  const parser = new DOMParser()
  const doc = parser.parseFromString(`<div id="__root">${safeHtml}</div>`, 'text/html')
  const ctx = collectText(doc)
  let hit = false
  // 1) 长句锚点（整句在文档中出现）
  for (const anchor of excerptAnchors(excerpt)) {
    if (markFirstInDoc(doc, ctx, normalizeText(anchor))) { hit = true; break }
  }
  // 2) 全文 excerpt 整段
  if (!hit) hit = markFirstInDoc(doc, ctx, normalizeText(stripMarkdown(excerpt)))
  // 3) 最长公共子串兜底（OCR/拼接类 excerpt 部分命中）
  if (!hit) hit = markLongestWindow(doc, ctx, excerpt)
  return { html: doc.getElementById('__root')?.innerHTML ?? safeHtml, hit }
}

// 渲染整篇文档并插入高亮。documentId 缺失或文档未加载时返回空。
function buildHighlightedHtml(
  documentId: string | undefined,
  excerpt: string,
): { html: string; hit: boolean; status: 'empty' | 'loading' | 'error' | 'ready' } {
  if (!documentId) return { html: '', hit: false, status: 'empty' }
  if (documentErrors.value[documentId]) return { html: '', hit: false, status: 'error' }
  const doc = documentCache.value[documentId]
  if (!doc) return { html: '', hit: false, status: 'loading' }
  const source = doc.format === 'markdown' ? (marked.parse(doc.content) as string) : doc.content
  const safe = DOMPurify.sanitize(source, {
    ADD_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td', 'img', 'mark'],
    ADD_ATTR: ['src', 'alt', 'style', 'border'],
  })
  const { html, hit } = highlightExcerpt(safe, excerpt)
  return { html, hit, status: 'ready' }
}

// —— 左侧列表按「证据」去重分组 ——
// 后端把同一处证据（同一对 left/right excerpt）按多条查重规则各判一次，
// 产生 N 个标题不同但证据相同的 finding。这里按归一化的「left+right 摘录对」
// 归并成一组，列表项 = 一处证据，使「列表项 ↔ 右侧高亮」一一对应。
interface EvidenceGroup {
  key: string
  representative: DuplicateResult
  members: DuplicateResult[]
  ruleNames: string[]
  maxScore: number
  verdict: DuplicateVerdict
}

// 左侧证据归一化文本（高亮基于左侧，故只按左侧分组）。
// 去掉开头的 image_N.png 文件名前缀——OCR 证据常以图片文件名打头，去掉后
// 「image_2.png 异步切换流程…」与「异步切换流程…」才能并入同一处证据。
function leftNormOf(finding: DuplicateResult): string {
  const cleaned = stripMarkdown(finding.left_excerpt).replace(/^image[_\d]*\.?png\s*/i, '')
  return normalizeText(cleaned)
}

// 按左侧证据去重分组，并用「包含合并」处理后端 grouper 的可变长度截断：
// 若某组的左侧归一化文本是另一组的【前缀】（短串被截断成长串的子集），则合并为同一处证据。
// representative 取组内左侧归一化文本最长者（最完整），保证高亮/标题信息最全。
const groupedFindings = computed<EvidenceGroup[]>(() => {
  // 1) 按精确左侧归一化文本先归并
  const map = new Map<string, { leftNorm: string; members: DuplicateResult[] }>()
  for (const finding of sortedFindings.value) {
    const leftNorm = leftNormOf(finding)
    const bucket = map.get(leftNorm)
    if (bucket) bucket.members.push(finding)
    else map.set(leftNorm, { leftNorm, members: [finding] })
  }
  let clusters = [...map.values()]
  // 2) 包含合并：短文本是长文本前缀 → 并入长文本（消除「同一段证据被截成不同长度」造成的重复）
  let changed = true
  while (changed) {
    changed = false
    outer: for (let i = 0; i < clusters.length; i++) {
      for (let j = 0; j < clusters.length; j++) {
        if (i === j) continue
        const A = clusters[i], B = clusters[j]
        if (A.leftNorm.length < B.leftNorm.length && B.leftNorm.startsWith(A.leftNorm)) {
          B.members = B.members.concat(A.members)
          clusters.splice(i, 1)
          changed = true
          break outer
        }
      }
    }
  }
  // 3) 按组内首条 finding 在原排序中的位置稳定排序（保持「按 A 文档位置」顺序）
  clusters.sort((a, b) => sortedFindings.value.indexOf(a.members[0]) - sortedFindings.value.indexOf(b.members[0]))
  // 4) 构造 EvidenceGroup，representative = 左侧归一化文本最长者
  return clusters.map((cluster, idx) => {
    const members = cluster.members
    const representative = [...members].sort(
      (a, b) => leftNormOf(b).length - leftNormOf(a).length,
    )[0]
    const ruleNames: string[] = []
    let maxScore = 0
    for (const m of members) {
      if (!ruleNames.includes(m.check_item_name)) ruleNames.push(m.check_item_name)
      maxScore = Math.max(maxScore, m.similarity_score)
    }
    return {
      key: `g${idx}-${cluster.leftNorm.slice(0, 16)}`,
      representative,
      members,
      ruleNames,
      maxScore,
      verdict: representative.verdict,
    }
  })
})

// 计算某 excerpt 在其原始文档里「真正会被高亮的那段」的归一化 needle（与
// highlightExcerpt 同一套三级匹配）。返回 needle，未命中返回 ''。groupTitle 与
// highlightExcerpt 共用同一 needle，保证「列表标题 = 右侧高亮」严格一致。
function excerptMatchNeedle(documentId: string | undefined, excerpt: string): string {
  if (!documentId || !excerpt) return ''
  const doc = documentCache.value[documentId]
  if (!doc) return ''
  const docNorm = normalizeText(stripMarkdown(doc.format === 'markdown' ? doc.content : doc.content).replace(/\\([^\s])/g, '$1'))
  const exNorm = normalizeText(stripMarkdown(excerpt))
  if (!docNorm || exNorm.length < 6) return ''
  // 1) 锚点（按长度降序，与 highlightExcerpt 一致）
  const frags = stripMarkdown(excerpt).split(/[。\n；;！!？?]/).map((s) => s.trim()).filter((s) => normalizeText(s).length >= 12).sort((a, b) => normalizeText(b).length - normalizeText(a).length)
  for (const f of frags) {
    const nf = normalizeText(f)
    if (nf.length >= 6 && docNorm.includes(nf)) return nf
  }
  // 2) 全文
  if (exNorm.length >= 6 && docNorm.includes(exNorm)) return exNorm
  // 3) LCS（与 markLongestWindow 同一套「中文字符数优先」择优，保证标题与高亮一致）
  const isCJK = (ch: string) => ch >= '\u4e00' && ch <= '\u9fff'
  const prevLen = new Int16Array(exNorm.length + 1), prevCjk = new Int16Array(exNorm.length + 1)
  const curLen = new Int16Array(exNorm.length + 1), curCjk = new Int16Array(exNorm.length + 1)
  let bestLen = 0, bestCjk = 0, bestEnd = 0
  for (let j = 1; j <= docNorm.length; j++) {
    for (let i = 1; i <= exNorm.length; i++) {
      if (exNorm[i - 1] === docNorm[j - 1]) {
        curLen[i] = (prevLen[i - 1] + 1) as number
        curCjk[i] = (prevCjk[i - 1] + (isCJK(exNorm[i - 1]) ? 1 : 0)) as number
        if (curCjk[i] > bestCjk || (curCjk[i] === bestCjk && curLen[i] > bestLen)) {
          bestCjk = curCjk[i]; bestLen = curLen[i]; bestEnd = i
        }
      } else { curLen[i] = 0; curCjk[i] = 0 }
    }
    prevLen.set(curLen); prevCjk.set(curCjk); curLen.fill(0); curCjk.fill(0)
  }
  if (bestCjk >= 4 || bestLen >= 8) return exNorm.slice(bestEnd - bestLen, bestEnd)
  return ''
}

// 把归一化 needle 映射回 excerpt 的显示文本（用于列表标题，保证可读）。
function needleToDisplay(excerpt: string, needle: string): string {
  if (!needle) return ''
  const clean = stripMarkdown(excerpt).replace(/\s+/g, ' ').trim()
  // 在 clean 里找首个归一化后包含 needle 的连续片段（按句切）
  const frags = clean.split(/[。\n；;！!？?]/).map((s) => s.trim())
  for (const f of frags) {
    if (normalizeText(f).includes(needle)) {
      return f.length > 24 ? f.slice(0, 24) + '…' : f
    }
  }
  // 退化：直接展示 needle 片段
  return needle.length > 24 ? needle.slice(0, 24) + '…' : needle
}

// 派生一处证据的标题：用「真正被高亮的那段」的显示文本，确保列表标题与右侧高亮一致。
// 文档未加载时退化为摘录首个有效片段，避免列表空标题。
function groupTitleOf(group: EvidenceGroup): string {
  const rep = group.representative
  const needle = excerptMatchNeedle(rep.left_document_id, rep.left_excerpt)
  if (needle) return needleToDisplay(rep.left_excerpt, needle)
  return excerptOwnHead(rep.left_excerpt) || rep.check_item_name
}

// 摘录自身的首个有效片段（按句切，去文件名前缀），用于冲突时的消歧标题。
function excerptOwnHead(excerpt: string): string {
  const clean = stripMarkdown(excerpt).replace(/^image[_\d]*\.?png\s*/i, '').replace(/\s+/g, ' ').trim()
  if (!clean) return ''
  return clean.length > 24 ? clean.slice(0, 24) + '…' : clean
}

// 全部证据组的标题映射（带冲突消歧）：默认用「高亮段」作标题；若多个组的标题撞车，
// 则改用各自摘录自身的首句，保证列表里每个标题唯一、互不重复。
const groupTitles = computed<Record<string, string>>(() => {
  const primary: Record<string, string> = {}
  for (const g of groupedFindings.value) primary[g.key] = groupTitleOf(g)
  // 检测冲突：相同标题出现多次
  const byTitle: Record<string, string[]> = {}
  for (const g of groupedFindings.value) {
    const t = primary[g.key]
    ;(byTitle[t] ||= []).push(g.key)
  }
  // 冲突组改用摘录首句消歧
  const result: Record<string, string> = {}
  for (const g of groupedFindings.value) {
    const t = primary[g.key]
    if (byTitle[t].length > 1) {
      result[g.key] = excerptOwnHead(g.representative.left_excerpt) || t
    } else {
      result[g.key] = t
    }
  }
  return result
})

function groupTitle(group: EvidenceGroup): string {
  return groupTitles.value[group.key] || groupTitleOf(group)
}

// A/B 两侧渲染结果（computed，依赖 selectedFinding + documentCache）。
const leftDocView = computed(() =>
  buildHighlightedHtml(selectedFinding.value?.left_document_id, selectedFinding.value?.left_excerpt ?? ''),
)
const rightDocView = computed(() =>
  buildHighlightedHtml(selectedFinding.value?.right_document_id, selectedFinding.value?.right_excerpt ?? ''),
)

// 加载单个原始文档：带 12s 超时兜底，失败记入 documentErrors。
async function loadDocument(id: string) {
  try {
    const fetchPromise = documentsApi.getContent(projectId.value, id)
    const timeout = new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error('timeout')), 12000),
    )
    documentCache.value = { ...documentCache.value, [id]: await Promise.race([fetchPromise, timeout]) }
  } catch {
    documentErrors.value = { ...documentErrors.value, [id]: true }
  }
}

// 结果加载后预取本任务涉及的【全部去重文档】。串行加载（非并发），避免 token
// 刷新拦截器在并发 401 下让某些请求永久挂起。docLoading 在预取期间为 true。
async function prefetchTaskDocuments() {
  const findings = data.value?.findings || []
  const idSet = new Set<string>()
  for (const f of findings) {
    if (f.left_document_id) idSet.add(f.left_document_id)
    if (f.right_document_id) idSet.add(f.right_document_id)
  }
  const ids = [...idSet].filter((id) => !documentCache.value[id] && !documentErrors.value[id])
  if (!ids.length) return
  docLoading.value = true
  for (const id of ids) {
    await loadDocument(id)
  }
  docLoading.value = false
}

// 清除某文档的失败标记并重新加载（面板「重试」按钮）。
function retryDocument(id: string) {
  documentErrors.value = { ...documentErrors.value, [id]: false }
  void loadDocument(id)
}

// 切换重复点时滚动到高亮段（文档已在 prefetchTaskDocuments 中加载）。
watch(selectedFinding, async () => {
  await nextTick()
  scrollHighlightIntoView()
})

// 原文渲染完成（computed status 变为 ready）后把高亮段滚动到面板中央。
// 用独立 watch 覆盖「文档已缓存立即渲染」与「异步加载完再渲染」两种时序。
function scrollHighlightIntoView() {
  // A、B 两侧各自独立滚动到自己的高亮段——两侧是独立滚动容器，必须分别定位，
  // 不能只滚一侧（否则切到 B 方证据不同的项时 B 方不会跳转）。
  for (const paneRef of [leftDocRef.value, rightDocRef.value]) {
    const hit = paneRef?.querySelector('.dup-hit') as HTMLElement | null
    hit?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }
}
watch([leftDocView, rightDocView], () => {
  if (leftDocView.value.status === 'ready' || rightDocView.value.status === 'ready') {
    nextTick(scrollHighlightIntoView)
  }
})

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '暂无'
}

function matrixStrength(pair: { max_evidence_strength: number | null; suspicious_count: number; finding_count: number }): string {
  const strength = Number(pair.max_evidence_strength || 0)
  if (pair.suspicious_count > 0 || strength >= 0.75) return 'matrix-high'
  if (strength >= 0.4 || pair.finding_count > 0) return 'matrix-medium'
  return 'matrix-low'
}

function clusterNames(cluster: DuplicateEvidenceCluster): string {
  const names = cluster.occurrences
    ?.map((item) => item.display_name || item.filename || item.document_id)
    .filter((value, index, values) => values.indexOf(value) === index)
  return (names || cluster.document_ids).join('、')
}

function viewTimeline() {
  if (!selectedTaskId.value) return
  router.push({
    name: 'duplicate-execution',
    params: { id: projectId.value },
    query: { taskId: selectedTaskId.value },
  })
}

function recheck() {
  Modal.confirm({
    title: '确认重新查重',
    content: '将创建新的查重任务，当前任务和结果会保留。',
    okText: '开始查重',
    cancelText: '取消',
    onOk: async () => {
      try {
        await duplicateApi.start(projectId.value)
        await router.push({ name: 'duplicate-execution', params: { id: projectId.value } })
      } catch (error: any) {
        const detail = error?.response?.data?.detail
        message.error(typeof detail === 'object' ? detail?.message : detail || '重新查重失败')
      }
    },
  })
}
</script>

<template>
  <div class="duplicate-results-view">
    <section class="result-header">
      <div class="result-title">
        <h1>{{ '标书查重结果' }}</h1>
        <p v-if="matrix && matrix.members.length > 2">
          批量文档：{{ batchDocuments.map((document) => document.duplicate_display_name || document.original_filename).join('、') }}
        </p>
        <p v-else>A 方：{{ leftDocument?.original_filename || '—' }}　　B 方：{{ rightDocument?.original_filename || '—' }}</p>
      </div>
      <div class="header-actions">
        <div class="task-control">
          <label for="duplicate-task-select">查重任务</label>
          <select id="duplicate-task-select" v-model="selectedTaskId">
            <option v-for="task in projectStore.reviewTasks" :key="task.id" :value="task.id">
              {{ formatDate(task.created_at) }} · {{ task.status === 'completed' ? '已完成' : task.status }}
            </option>
          </select>
        </div>
        <button v-if="authStore.isInteriorUser" @click="viewTimeline">查看执行时间线</button>
        <button class="primary" @click="recheck">重新查重</button>
        <button @click="router.push({ name: 'history' })">返回历史标书</button>
      </div>
    </section>

    <a-spin :spinning="loading">
      <template v-if="data">
        <a-alert
          v-if="data.summary.completed_rule_count < data.summary.rule_count"
          type="warning"
          show-icon
          message="部分查重规则执行失败，当前结果可能不完整，请结合各规则状态复核。"
        />
        <a-alert
          v-if="data.summary.coverage_status && data.summary.coverage_status !== 'complete'"
          type="warning"
          show-icon
          :message="`文档解析覆盖度为${data.summary.coverage_status === 'insufficient' ? '不足' : '部分'}，未发现重复不能视为绝对结论。`"
          :description="(data.summary.coverage_warnings || []).join('；') || '请打开解析诊断查看未覆盖对象。'"
        />
        <section class="summary-grid">
          <div><strong>{{ data.summary.rule_count }}</strong><span>规则子代理</span></div>
          <div><strong>{{ data.summary.completed_rule_count }}</strong><span>已完成</span></div>
          <div class="reasonable"><strong>{{ data.summary.reasonable_count }}</strong><span>合理重复</span></div>
          <div class="suspicious"><strong>{{ data.summary.suspicious_count }}</strong><span>疑似不合理重复</span></div>
          <div class="unknown"><strong>{{ data.summary.unknown_count || 0 }}</strong><span>证据不足</span></div>
        </section>

        <section v-if="matrix && matrix.members.length > 2" class="rule-section matrix-section">
          <header>
            <div>
              <h2>文档对证据矩阵</h2>
              <span class="status completed">仅表示证据强度 / 需复核程度</span>
            </div>
            <span>{{ matrix.members.length }} 份文档 · {{ matrix.pairs.length }} 个文档对</span>
          </header>
          <div class="matrix-table-wrap">
            <table class="matrix-table">
              <thead>
                <tr>
                  <th>文档</th>
                  <th v-for="member in matrix.members" :key="member.document_id">
                    {{ member.display_name || member.filename || member.party_key }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in matrix.members" :key="row.document_id">
                  <th>{{ row.display_name || row.filename || row.party_key }}</th>
                  <td v-for="column in matrix.members" :key="column.document_id">
                    <template v-if="row.document_id === column.document_id">—</template>
                    <template v-else>
                      <div
                        v-for="pair in matrix.pairs.filter((item) =>
                          (item.left_document_id === row.document_id && item.right_document_id === column.document_id) ||
                          (item.left_document_id === column.document_id && item.right_document_id === row.document_id),
                        )"
                        :key="pair.id"
                        :class="['matrix-cell', matrixStrength(pair)]"
                        :title="`finding ${pair.finding_count} · evidence ${Math.round(Number(pair.max_evidence_strength || 0) * 100)}%`"
                      >
                        {{ pair.finding_count }} / {{ Math.round(Number(pair.max_evidence_strength || 0) * 100) }}%
                      </div>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-if="clusters.length" class="rule-section cluster-section">
          <header>
            <div>
              <h2>跨文档证据簇</h2>
              <span class="status completed">保留全部 occurrences</span>
            </div>
            <span>{{ clusters.length }} 个证据簇</span>
          </header>
          <article v-for="cluster in clusters.slice(0, 50)" :key="cluster.id" class="cluster-card">
            <div class="cluster-head">
              <strong>{{ cluster.content_type }} · {{ cluster.occurrence_count }} 处</strong>
              <span>证据强度 {{ evidenceStrength(cluster.evidence_strength) }}</span>
            </div>
            <p class="cluster-docs">文档：{{ clusterNames(cluster) }}</p>
            <blockquote>{{ cluster.representative_excerpt }}</blockquote>
            <div v-if="cluster.occurrences?.length" class="cluster-occurrences">
              <div v-for="occurrence in cluster.occurrences" :key="occurrence.id">
                <span>{{ occurrence.display_name || occurrence.filename || occurrence.document_id }}</span>
                <small>{{ occurrence.location?.section || '位置未知' }}</small>
                <p>{{ occurrence.excerpt }}</p>
              </div>
            </div>
          </article>
        </section>

        <!-- 结构化表格对照区块按用户要求隐藏：重复点段直接展示，如需恢复去掉此注释即可。
        <section v-if="tableComparisons.length || tableWarnings.length" class="rule-section table-section">
          <header>
            <div>
              <h2>结构化表格对照</h2>
              <span class="status completed">独立通道</span>
            </div>
            <span>{{ tableComparisons.length }} 组行列候选</span>
          </header>
          <a-alert
            v-if="tableWarnings.length"
            type="warning"
            show-icon
            :message="tableWarnings.join('；')"
          />
          <div v-for="comparison in tableComparisons.slice(0, 20)" :key="comparison.table_candidate_id" class="table-comparison">
            <div class="table-score-row">
              <strong>证据强度 {{ Math.round(comparison.score * 100) }}%</strong>
              <span>表头 {{ Math.round(comparison.header_similarity * 100) }}%</span>
              <span>行对齐 {{ Math.round(comparison.row_alignment_score * 100) }}%</span>
              <span>数字签名 {{ Math.round(comparison.numeric_signature_score * 100) }}%</span>
              <span>罕见单元格 {{ Math.round(comparison.rare_cell_overlap * 100) }}%</span>
              <span>结构 {{ Math.round(comparison.table_structure_score * 100) }}%</span>
            </div>
            <div class="evidence-grid">
              <div>
                <h3>A 方 · {{ (comparison.left.section_path || []).join(' / ') || '表格' }}</h3>
                <small>表 {{ comparison.left.table_id }} · 行 {{ Number(comparison.left.row_index) + 1 }}</small>
                <div class="cell-row">
                  <span v-for="(cell, index) in comparison.left.cells" :key="index">{{ cell }}</span>
                </div>
              </div>
              <div>
                <h3>B 方 · {{ (comparison.right.section_path || []).join(' / ') || '表格' }}</h3>
                <small>表 {{ comparison.right.table_id }} · 行 {{ Number(comparison.right.row_index) + 1 }}</small>
                <div class="cell-row">
                  <span v-for="(cell, index) in comparison.right.cells" :key="index">{{ cell }}</span>
                </div>
              </div>
            </div>
            <p v-if="comparison.shared_rare_cells.length" class="rare-cells">
              共同罕见值：{{ comparison.shared_rare_cells.join('、') }}
            </p>
          </div>
        </section>
        -->

        <section class="rule-section duplicate-list-section">
          <header>
            <div>
              <h2>重复点（按 A 文档位置排序）</h2>
              <span class="status completed">已按证据去重，点击查看 A/B 重复点说明</span>
            </div>
            <span>{{ groupedFindings.length }} 处证据 · {{ sortedFindings.length }} 条规则命中</span>
          </header>

          <div v-if="sortedFindings.length === 0" class="empty-rule">
            未发现达到报告门槛的重复点
          </div>

          <div v-else class="duplicate-list-layout">
            <ul class="finding-list">
              <li
                v-for="(group, index) in groupedFindings"
                :key="group.key"
                :class="{ active: group.key === selectedGroupKey }"
                @click="selectedGroupKey = group.key"
              >
                <span class="idx">{{ index + 1 }}</span>
                <div class="grp-main">
                  <strong :title="group.representative.check_item_name">{{ groupTitle(group) }}</strong>
                  <span v-if="group.ruleNames.length > 1" class="grp-rules">命中 {{ group.ruleNames.length }} 条规则</span>
                </div>
                <span class="score">相似度 {{ Math.round(group.maxScore * 100) }}%</span>
              </li>
            </ul>

            <div class="finding-detail">
              <article v-if="selectedFinding" :class="['finding-card', selectedFinding.verdict]">
                <div class="finding-head">
                  <span :class="['verdict', selectedFinding.verdict]">{{ verdictLabel(selectedFinding.verdict) }}</span>
                  <strong>{{ selectedGroup ? groupTitle(selectedGroup) : selectedFinding.check_item_name }}</strong>
                  <span class="match-type">{{ matchTypeLabel(selectedFinding.match_type) }}</span>
                  <span class="score">相似度 {{ Math.round(selectedFinding.similarity_score * 100) }}%</span>
                  <span class="source-basis">{{ sourceBasisLabel(selectedFinding.source_basis) }}</span>
                  <span class="evidence-strength">证据强度 {{ evidenceStrength(selectedFinding.evidence?.evidence_strength) }}</span>
                  <span v-if="Number(selectedFinding.evidence?.collapsed_count) > 1" class="aggregate-count">
                    已合并 {{ selectedFinding.evidence?.collapsed_count }} 处
                  </span>
                </div>
                <div v-if="selectedGroup && selectedGroup.ruleNames.length > 1" class="rule-chips">
                  <span class="rule-chips-label">命中规则：</span>
                  <span v-for="name in selectedGroup.ruleNames" :key="name" class="rule-chip" :title="name">{{ name }}</span>
                </div>

                <!-- 原始文档对照：并排显示 A/B 整篇原文，高亮疑似重复段并自动滚动定位 -->
                <section class="doc-compare">
                  <h3 class="doc-compare-title">原始文档对照<span class="doc-compare-sub">（高亮为疑似重复段）</span></h3>
                  <div class="doc-compare-grid">
                    <div class="doc-pane">
                      <div class="doc-pane-head">
                        <span class="doc-tag doc-tag-a">A 方</span>
                        <strong>{{ selectedFinding.left_filename || 'A 文档' }}</strong>
                        <small>{{ locationText(selectedFinding.left_location) }}</small>
                      </div>
                      <a-spin :spinning="docLoading && leftDocView.status === 'loading'">
                        <div v-if="leftDocView.status === 'ready'" ref="leftDocRef" class="doc-pane-body" v-html="leftDocView.html" />
                        <div v-else-if="leftDocView.status === 'error'" class="doc-pane-empty">
                          原文加载失败，见下方摘录
                          <button class="retry-btn" @click="selectedFinding && retryDocument(selectedFinding.left_document_id)">重试</button>
                        </div>
                        <div v-else-if="leftDocView.status === 'empty'" class="doc-pane-empty">无 A 方原始文档</div>
                        <div v-else class="doc-pane-empty">正在加载原文…</div>
                      </a-spin>
                      <p v-if="leftDocView.status === 'ready' && !leftDocView.hit" class="no-hit-hint">
                        未能在原文中精确定位该片段，请见下方摘录对照
                      </p>
                    </div>
                    <div class="doc-pane">
                      <div class="doc-pane-head">
                        <span class="doc-tag doc-tag-b">B 方</span>
                        <strong>{{ selectedFinding.right_filename || 'B 文档' }}</strong>
                        <small>{{ locationText(selectedFinding.right_location) }}</small>
                      </div>
                      <a-spin :spinning="docLoading && rightDocView.status === 'loading'">
                        <div v-if="rightDocView.status === 'ready'" ref="rightDocRef" class="doc-pane-body" v-html="rightDocView.html" />
                        <div v-else-if="rightDocView.status === 'error'" class="doc-pane-empty">
                          原文加载失败，见下方摘录
                          <button class="retry-btn" @click="selectedFinding && retryDocument(selectedFinding.right_document_id)">重试</button>
                        </div>
                        <div v-else-if="rightDocView.status === 'empty'" class="doc-pane-empty">无 B 方原始文档</div>
                        <div v-else class="doc-pane-empty">正在加载原文…</div>
                      </a-spin>
                      <p v-if="rightDocView.status === 'ready' && !rightDocView.hit" class="no-hit-hint">
                        未能在原文中精确定位该片段，请见下方摘录对照
                      </p>
                    </div>
                  </div>
                </section>

                <h3 class="excerpt-title">摘录速览</h3>
                <div class="evidence-grid">
                  <div>
                    <h3>A 方证据</h3>
                    <small>{{ selectedFinding.left_filename }} · {{ locationText(selectedFinding.left_location) }}</small>
                    <img
                      v-if="selectedFinding.left_location?.thumbnail_url"
                      class="evidence-image"
                      :src="selectedFinding.left_location.thumbnail_url"
                      alt="A 方图片证据"
                    />
                    <blockquote>{{ selectedFinding.left_excerpt }}</blockquote>
                  </div>
                  <div>
                    <h3>B 方证据</h3>
                    <small>{{ selectedFinding.right_filename }} · {{ locationText(selectedFinding.right_location) }}</small>
                    <img
                      v-if="selectedFinding.right_location?.thumbnail_url"
                      class="evidence-image"
                      :src="selectedFinding.right_location.thumbnail_url"
                      alt="B 方图片证据"
                    />
                    <blockquote>{{ selectedFinding.right_excerpt }}</blockquote>
                  </div>
                </div>

                <div class="explanation">
                  <b>判断理由：</b>{{ selectedFinding.explanation }}
                  <p v-if="selectedFinding.suggestion"><b>处理建议：</b>{{ selectedFinding.suggestion }}</p>
                </div>
                <div v-if="selectedFinding.evidence?.image_comparison" class="image-metrics">
                  图片通道 {{ evidenceStrength(selectedFinding.evidence.image_comparison.image_score) }} ·
                  A hash {{ String(selectedFinding.evidence.image_comparison.left_image_sha256 || '—').slice(0, 12) }} ·
                  B hash {{ String(selectedFinding.evidence.image_comparison.right_image_sha256 || '—').slice(0, 12) }}
                </div>
                <div v-if="selectedFinding.evidence?.source_reference" class="source-reference">
                  <b>可追溯来源：</b>
                  {{ selectedFinding.evidence.source_reference.source_filename }} ·
                  {{ selectedFinding.evidence.source_reference.source_version }} ·
                  hash {{ String(selectedFinding.evidence.source_reference.source_snapshot_hash).slice(0, 16) }}
                  <blockquote>{{ selectedFinding.evidence.source_reference.source_excerpt }}</blockquote>
                </div>
              </article>
              <div v-else class="empty-rule">点击左侧列表项查看重复点详情</div>
            </div>
          </div>
        </section>
      </template>
      <div v-else-if="!loading" class="empty-page">暂无可展示的查重结果</div>
    </a-spin>
  </div>
</template>

<style scoped>
.duplicate-results-view { display: flex; flex-direction: column; gap: 18px; }
.result-header, .rule-section { background: #fff; border: 1px solid #e6e8ee; border-radius: 9px; padding: 20px; }
.result-header { display: flex; align-items: center; gap: 24px; }
.result-title { min-width: 0; flex: 1; }
.result-header h1 { margin: 0 0 6px; font-size: 22px; }
.result-header p { margin: 0; color: #777; }
.header-actions { display: flex; align-items: center; gap: 10px; margin-left: auto; white-space: nowrap; }
.task-control { display: flex; align-items: center; gap: 10px; }
button { border: 1px solid #d4d7df; background: #fff; border-radius: 6px; padding: 8px 14px; cursor: pointer; }
button.primary { border-color: #d7041a; background: #d7041a; color: #fff; }
.task-control label { font-weight: 600; }
.task-control select { min-width: 260px; padding: 8px; border: 1px solid #d4d7df; border-radius: 5px; }
.summary-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
.summary-grid > div { background: #fff; border: 1px solid #e6e8ee; border-radius: 9px; padding: 18px; display: flex; flex-direction: column; }
.summary-grid strong { font-size: 28px; }
.summary-grid span { color: #777; }
.summary-grid .reasonable strong { color: #18864b; }
.summary-grid .suspicious strong { color: #d7041a; }
.summary-grid .unknown strong { color: #b77900; }
.matrix-table-wrap { overflow-x: auto; }
.matrix-table { width: 100%; border-collapse: collapse; min-width: 720px; }
.matrix-table th, .matrix-table td { border: 1px solid #e1e4eb; padding: 8px; text-align: center; }
.matrix-table th { background: #f7f8fa; text-align: left; }
.matrix-cell { border-radius: 4px; padding: 6px 4px; font-size: 12px; }
.matrix-high { color: #9d1c1c; background: #ffe4e4; }
.matrix-medium { color: #805c00; background: #fff3cd; }
.matrix-low { color: #4f6474; background: #eef3f7; }
.cluster-card { border: 1px solid #e2e5eb; border-radius: 7px; padding: 13px; margin-top: 10px; }
.cluster-head { display: flex; justify-content: space-between; gap: 12px; }
.cluster-docs { margin: 7px 0; color: #666; }
.cluster-occurrences { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; margin-top: 10px; }
.cluster-occurrences > div { background: #fafbfc; border-radius: 5px; padding: 8px; }
.cluster-occurrences small { display: block; color: #888; margin-top: 3px; }
.cluster-occurrences p { margin: 5px 0 0; white-space: pre-wrap; }
.rule-section > header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #eee; padding-bottom: 12px; margin-bottom: 14px; }
.rule-section h2 { display: inline; margin: 0 10px 0 0; font-size: 18px; }
.status { font-size: 12px; border-radius: 10px; padding: 2px 8px; background: #eee; }
.status.completed { color: #18864b; background: #eaf7f0; }
.status.failed { color: #c62828; background: #fff0f0; }
/* 左列表 + 右详情布局：导航栏固定高度、独立滚动；详情区随页面滚动 */
.duplicate-list-layout { display: grid; grid-template-columns: minmax(280px, 380px) 1fr; gap: 16px; align-items: start; }
.finding-list { list-style: none; margin: 0; padding: 6px; display: flex; flex-direction: column; gap: 6px; max-height: 70vh; overflow-y: auto; position: sticky; top: 12px; }
.finding-list li { cursor: pointer; padding: 7px 10px; border: 1px solid #e2e5eb; border-radius: 6px; display: flex; flex-wrap: nowrap; align-items: center; gap: 6px 8px; background: #fff; }
.finding-list li:hover { border-color: #c0c4cc; background: #fafbfc; }
.finding-list li.active { border-color: #d7041a; background: #fff5f6; }
.finding-list .idx { flex: 0 0 auto; min-width: 20px; height: 20px; line-height: 20px; text-align: center; border-radius: 50%; background: #eef0f4; color: #555; font-size: 11px; }
.finding-list li.active .idx { background: #d7041a; color: #fff; }
.finding-list .verdict { flex: 0 0 auto; }
.finding-list strong { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.finding-list .loc { flex: 1 1 100%; color: #888; font-size: 12px; }
.finding-list .score { flex: 0 0 auto; font-weight: 600; color: #d7041a; font-size: 12px; }
.finding-list small { flex: 1 1 100%; color: #999; font-size: 11px; }
.finding-list .grp-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.finding-list .grp-rules { font-size: 11px; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rule-chips { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 8px; }
.rule-chips-label { color: #888; font-size: 12px; }
.rule-chip { display: inline-block; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; background: #eef0f4; color: #4a4f5a; border-radius: 4px; padding: 2px 8px; font-size: 12px; }
.finding-detail { min-width: 0; }
.finding-card { border: 1px solid #e2e5eb; border-left-width: 4px; border-radius: 7px; padding: 16px; margin-top: 0; }
.finding-card.reasonable { border-left-color: #18864b; }
.finding-card.suspicious { border-left-color: #d7041a; }
.finding-card.unknown { border-left-color: #d99b13; }
.finding-head { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.finding-head strong { min-width: 220px; flex: 1; }
.verdict { border-radius: 4px; padding: 3px 8px; font-size: 12px; }
.verdict.reasonable { color: #18864b; background: #eaf7f0; }
.verdict.suspicious { color: #d7041a; background: #fff0f0; }
.verdict.unknown { color: #8b6400; background: #fffbe6; }
.match-type { color: #666; font-size: 12px; }
.score { font-weight: 600; color: #d7041a; }
.source-basis, .evidence-strength, .aggregate-count { color: #777; font-size: 12px; }
.aggregate-count { color: #6c4fa3; }
.evidence-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 14px; }
.evidence-grid > div { background: #fafbfc; border-radius: 6px; padding: 13px; }
.evidence-grid h3 { margin: 0 0 4px; font-size: 14px; }
.evidence-grid small { color: #888; }
.evidence-image { display: block; max-width: 100%; max-height: 280px; margin-top: 10px; border: 1px solid #e0e3e9; border-radius: 4px; object-fit: contain; background: #fff; }
blockquote { margin: 10px 0 0; padding-left: 12px; border-left: 2px solid #ccd1db; white-space: pre-wrap; color: #444; }
.explanation { background: #f7f8fa; margin-top: 12px; padding: 12px; line-height: 1.6; }
.explanation p { margin: 6px 0 0; }
.source-reference { margin-top: 12px; padding: 12px; border: 1px solid #dfe7f2; background: #f7fbff; border-radius: 6px; }
.image-metrics { margin-top: 10px; color: #666; font-size: 12px; }
.table-comparison { padding: 14px 0; border-top: 1px solid #eef0f4; }
.table-comparison:first-of-type { border-top: 0; }
.table-score-row { display: flex; flex-wrap: wrap; gap: 8px 16px; color: #666; font-size: 12px; }
.table-score-row strong { color: #d7041a; font-size: 14px; }
.cell-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); margin-top: 10px; border: 1px solid #e0e3e9; }
.cell-row span { padding: 7px; border-right: 1px solid #e0e3e9; word-break: break-all; }
.rare-cells { margin: 8px 0 0; color: #8b6400; }
.empty-rule, .empty-page { color: #999; text-align: center; padding: 28px; }
/* 原始文档对照区块 */
.doc-compare { margin-top: 14px; }
.doc-compare-title { margin: 0 0 10px; font-size: 15px; }
.doc-compare-sub { color: #999; font-weight: normal; font-size: 12px; }
.excerpt-title { margin: 16px 0 0; font-size: 13px; color: #888; font-weight: 600; }
.doc-compare-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.doc-pane { display: flex; flex-direction: column; border: 1px solid #e2e5eb; border-radius: 8px; background: #fff; overflow: hidden; }
.doc-pane-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #eef0f4; background: #fafbfc; }
.doc-pane-head strong { font-size: 13px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-pane-head small { color: #999; font-size: 11px; margin-left: auto; white-space: nowrap; }
.doc-tag { flex: 0 0 auto; font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 10px; color: #fff; }
.doc-tag-a { background: #2c6ecb; }
.doc-tag-b { background: #b04a1e; }
.doc-pane-body { max-height: 60vh; overflow-y: auto; padding: 12px 16px; line-height: 1.75; font-size: 14px; word-break: break-word; }
.doc-pane :deep(p) { margin: 6px 0; }
.doc-pane :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; }
.doc-pane :deep(th), .doc-pane :deep(td) { border: 1px solid #dfe3ea; padding: 5px 8px; font-size: 13px; }
.doc-pane :deep(th) { background: #f5f7fa; }
.doc-pane :deep(img) { max-width: 100%; height: auto; }
.doc-pane :deep(.dup-hit) { background: #fff3cd; border-radius: 3px; box-shadow: 0 0 0 2px #ffb300; padding: 0 2px; color: #5a4500; animation: dup-pulse 1.2s ease-out 2; }
.doc-pane-empty { color: #999; font-size: 13px; padding: 24px; text-align: center; }
.retry-btn { margin-left: 8px; border: 1px solid #d4d7df; background: #fff; border-radius: 5px; padding: 3px 12px; font-size: 12px; cursor: pointer; color: #555; }
.retry-btn:hover { border-color: #d7041a; color: #d7041a; }
.no-hit-hint { margin: 0; padding: 6px 12px 10px; color: #b77900; font-size: 12px; background: #fffbe6; border-top: 1px solid #fcefc4; }
@keyframes dup-pulse {
  0% { box-shadow: 0 0 0 2px #ffb300, 0 0 0 0 rgba(255, 179, 0, 0.5); }
  70% { box-shadow: 0 0 0 2px #ffb300, 0 0 0 10px rgba(255, 179, 0, 0); }
  100% { box-shadow: 0 0 0 2px #ffb300, 0 0 0 0 rgba(255, 179, 0, 0); }
}
.empty-rule, .empty-page { color: #999; text-align: center; padding: 28px; }
@media (max-width: 1200px) {
  .result-header { align-items: stretch; flex-direction: column; }
  .header-actions { margin-left: 0; }
}
@media (max-width: 900px) {
  .header-actions { flex-wrap: wrap; white-space: normal; }
  .task-control { width: 100%; }
  .task-control select { min-width: 0; flex: 1; }
  .summary-grid { grid-template-columns: 1fr 1fr; }
  .duplicate-list-layout { grid-template-columns: 1fr; }
  .finding-list { position: static; max-height: none; }
  .evidence-grid { grid-template-columns: 1fr; }
  .doc-compare-grid { grid-template-columns: 1fr; }
}
</style>
