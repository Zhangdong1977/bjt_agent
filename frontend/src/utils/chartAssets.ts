/**
 * 标书生成图表资产：LLM 在章节 Markdown 中输出 ```mermaid 代码块，
 * 由本模块在 WebView2（用户机器，中文字体天然可用）内渲染为 PNG dataURL，
 * 随 bjt.vsto.insert 的 images 附件发给 VSTO 插入 Word。
 *
 * 服务端不做 mermaid 渲染：生产 4 台 + 3 节点均为最小化 Ubuntu，装
 * node/chromium/CJK 字体的部署成本与风险远高于前端现成 Chromium 环境。
 *
 * 安全说明：本模块产出的 SVG 一律经 <img> 沙箱消费（禁脚本/禁外链），
 * 不做 DOMPurify 整段消毒——那会剥掉 foreignObject 内的 HTML 标签导致
 * 流程图节点文字丢失（详见 renderMermaidSvg 注释）。
 */

const PNG_SCALE = 2;
const PNG_MAX_WIDTH_PX = 2200;
const TITLE_LINE = /^\s*(?:title\s*[:：]?)\s*(.+?)\s*$/;

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;
let renderSeq = 0;

function ensureMermaid() {
  if (!mermaidReady) {
    mermaidReady = import("mermaid").then((mod) => {
      const mermaid = mod.default;
      mermaid.initialize({
        startOnLoad: false,
        theme: "default",
        securityLevel: "strict",
        // mermaid.render 解析失败时不再把"Syntax error in text…"错误 SVG 追加到页面
        // DOM（TaskPane 里会直接被用户看到）；异常仍会 throw，由调用方降级处理。
        suppressErrorRendering: true,
        fontFamily: '"Microsoft YaHei", "PingFang SC", Arial, sans-serif',
        // useMaxWidth:false 让 SVG 根节点输出像素级 width/height（默认 100% 会导致
        // 光栅化时尺寸解析歧义）；htmlLabels 保持默认开启，中文标签才能正常
        // 换行与内边距（WebView2/Chromium 支持 foreignObject 光栅化）。
        flowchart: { useMaxWidth: false, curve: "basis" },
        // topAxis 让 gantt 在顶部追加时间轴（mermaid 底轴仍会画，由
        // stripGanttBottomAxis 渲染后删除，最终时间轴只在最上部）
        gantt: { useMaxWidth: false, topAxis: true },
        sequence: { useMaxWidth: false },
        pie: { useMaxWidth: false },
      });
      return mermaid;
    });
  }
  return mermaidReady;
}

export interface MarkdownSegment {
  kind: "text" | "mermaid";
  content: string;
}

/** 把 Markdown 按 ```mermaid 围栏拆成 文本/图表 片段（非 mermaid 代码块保持原样归入文本）。 */
export function splitMermaidFences(markdown: string): MarkdownSegment[] {
  const segments: MarkdownSegment[] = [];
  const lines = (markdown || "").split("\n");
  let text: string[] = [];
  let index = 0;
  while (index < lines.length) {
    const trimmed = lines[index].trim();
    if (trimmed.startsWith("```")) {
      const language = trimmed.slice(3).trim().toLowerCase();
      let closing = index + 1;
      while (closing < lines.length && !lines[closing].trim().startsWith("```")) closing += 1;
      if (closing < lines.length) {
        if (language === "mermaid") {
          if (text.length) {
            segments.push({ kind: "text", content: text.join("\n") });
            text = [];
          }
          segments.push({ kind: "mermaid", content: lines.slice(index + 1, closing).join("\n") });
        } else {
          text = text.concat(lines.slice(index, closing + 1));
        }
        index = closing + 1;
        continue;
      }
    }
    text.push(lines[index]);
    index += 1;
  }
  if (text.length) segments.push({ kind: "text", content: text.join("\n") });
  return segments;
}

/** 渲染 mermaid 为 SVG 字符串；失败返回 null（调用方降级）。
 *
 * 不做 DOMPurify 消毒：mermaid securityLevel:'strict' 已对标签内容消毒，
 * 且本模块的两条消费路径均为 <img> 沙箱（禁脚本/禁外链）——曾用 DOMPurify
 * 整段消毒，会把 foreignObject 内的 HTML 标签（htmlLabels 的中文文字载体）
 * 按 SVG 命名空间规则剥掉，导致流程图节点文字全部消失。 */
export async function renderMermaidSvg(code: string): Promise<string | null> {
  try {
    const mermaid = await ensureMermaid();
    renderSeq += 1;
    const { svg } = await mermaid.render(
      `bjt-chart-${renderSeq}-${Date.now().toString(36)}`,
      prepareGanttForRender(code.trim()),
    );
    return svg ? stripGanttBottomAxis(svg) : null;
  } catch {
    return null;
  }
}

/**
 * gantt 时间轴置顶：mermaid 开启 topAxis 后只是在顶部"追加"一条轴，底部
 * 原轴仍然绘制。渲染后识别两个 g.grid 组——顶轴 transform 是
 * translate(sidePad, topPad)、底轴是 translate(sidePad, h-50)——删掉
 * translateY 较大的底轴（顶轴自带贯穿网格线），得到 Project 横道图式的
 * "时间轴只在图最上部"（2026-09-07 真机反馈：时间显示在了下部）。
 * 非 gantt 图没有 g.grid 组、或组数不为 2，原样返回不受影响。
 */
function stripGanttBottomAxis(svgText: string): string {
  const svg = parseSvgFragment(svgText);
  if (!svg) return svgText;
  const grids = svg.querySelectorAll("g.grid");
  if (grids.length !== 2) return svgText;
  const withY: Array<{ el: Element; y: number }> = [];
  grids.forEach((grid) => {
    const match = (grid.getAttribute("transform") || "").match(
      /translate\(\s*[\d.]+\s*,\s*([\d.]+)\s*\)/,
    );
    const y = match ? Number.parseFloat(match[1] || "") : Number.NaN;
    if (Number.isFinite(y)) withY.push({ el: grid, y });
  });
  if (withY.length === 2) {
    const bottom = withY[0]!.y > withY[1]!.y ? withY[0]!.el : withY[1]!.el;
    bottom.parentNode?.removeChild(bottom);
  }
  return new XMLSerializer().serializeToString(svg);
}

/** 解析 mermaid 输出的 SVG 字符串为 DOM 元素。
 *
 * 必须用 text/html 而非 image/svg+xml 解析：htmlLabels 开启时 mermaid 的节点
 * 标签里可含 <br> 等 HTML 写法（LLM 生成中文多行标签时大量使用），SVG 字符串
 * 因此不是良构 XML——按 XML 解析会报 "tag mismatch" 并得到 parsererror 文档
 * （2026-09-03 真机：11/19 块光栅化 invalid svg，全部降级成代码文本进 Word）。
 * HTML 解析器对 foreignObject 内的 HTML 内容宽容，随后用 XMLSerializer 序列化
 * 即得良构 XML（<br> 自闭合、文本转义），<img>/canvas 均可正常消费。 */
function parseSvgFragment(svgText: string): Element | null {
  const doc = new DOMParser().parseFromString(svgText, "text/html");
  const svg = doc.querySelector("svg");
  return svg && svg.namespaceURI === "http://www.w3.org/2000/svg" ? svg : null;
}

/** SVG 字符串 → data URL（<img> 沙箱加载，预览与光栅化共用，所见即所得）。 */
export function svgToDataUrl(svgText: string): string {
  const svg = parseSvgFragment(svgText);
  if (svg) {
    // <img> 需要显式尺寸；仅有 viewBox 时从其推导，避免渲染为 0 宽
    const viewBox = (svg.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(Number);
    if (!svg.getAttribute("width") && viewBox.length === 4 && viewBox[2] > 0) {
      svg.setAttribute("width", String(viewBox[2]));
      svg.setAttribute("height", String(viewBox[3]));
    }
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(svg));
  }
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgText);
}

/** 尽力从 mermaid 源码开头取 title（gantt/pie 支持），用作图表题注。 */
export function mermaidTitle(code: string): string {
  for (const line of (code || "").split("\n").slice(0, 8)) {
    const match = line.match(TITLE_LINE);
    if (match) return match[1].slice(0, 60);
  }
  return "";
}

const TICK_INTERVAL_LINE = /^\s*tickInterval\s+\S+/i;

/**
 * gantt 渲染前的健壮性预处理（仅 gantt 生效，其他图原样返回）：
 *
 * 1. 剥掉 title 行——Word 题注已带图名（mermaidTitle 提取），图内 title 与题注
 *    重复，且长中文标题会被 mermaid 画布右缘裁剪（2026-09-07 真机：图 3 标题
 *    只显示到"（示意"）。
 * 2. 无 tickInterval 时按任务日期跨度注入——短跨度（月内）时 mermaid 自动按天
 *    出刻度，"01-06""01-07"…标签互相重叠不可读（同日真机：21 天跨度 21 个刻度
 *    挤成一团；46 天跨度自动周刻度则正常）。>10 天注 1week、>70 天注 1month。
 */
function prepareGanttForRender(code: string): string {
  const lines = (code || "").split("\n");
  if ((lines[0] || "").trim() !== "gantt") return code;

  const hasTickInterval = lines.some((line) => TICK_INTERVAL_LINE.test(line));

  const stripped: string[] = [];
  let titleRemoved = false;
  for (const line of lines) {
    if (!titleRemoved && TITLE_LINE.test(line)) {
      titleRemoved = true;
      continue;
    }
    stripped.push(line);
  }

  if (!hasTickInterval) {
    const dates = (stripped.join("\n").match(/\d{4}-\d{2}-\d{2}/g) || []).sort();
    if (dates.length >= 2) {
      const spanDays = (Date.parse(dates[dates.length - 1] || "") - Date.parse(dates[0] || "")) / 86400000;
      const interval = spanDays > 70 ? "1month" : spanDays > 10 ? "1week" : "";
      if (interval) {
        let insertAt = stripped.findIndex((line) => /^\s*axisFormat\b/.test(line));
        if (insertAt < 0) insertAt = 0; // 紧跟 gantt 首行
        stripped.splice(insertAt + 1, 0, `    tickInterval ${interval}`);
      }
    }
  }
  return stripped.join("\n");
}

/** 仅接受纯像素数值（"800"/"800px"）；"100%" 之类的相对值返回 NaN。 */
function parsePixelLength(value: string | null): number {
  if (!value) return NaN;
  const text = value.trim();
  if (!/^\d+(?:\.\d+)?px?$/.test(text)) return NaN;
  const parsed = parseFloat(text);
  return Number.isFinite(parsed) ? parsed : NaN;
}

async function svgToPngDataUrl(svgText: string): Promise<string> {
  const svg = parseSvgFragment(svgText);
  if (!svg) throw new Error("invalid svg");
  // viewBox 是设计坐标系，优先于 width/height 属性（属性可能是 "100%" 或带 max-width
  // 的 style，直接 parseFloat 会得到错误尺寸——曾导致画布被压扁、Word 里模糊变形）。
  const viewBox = (svg.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(Number);
  const viewBoxWidth = viewBox.length === 4 && Number.isFinite(viewBox[2]) ? viewBox[2] : 0;
  const viewBoxHeight = viewBox.length === 4 && Number.isFinite(viewBox[3]) ? viewBox[3] : 0;
  const width = viewBoxWidth > 0 ? viewBoxWidth : parsePixelLength(svg.getAttribute("width"));
  const height = viewBoxHeight > 0 ? viewBoxHeight : parsePixelLength(svg.getAttribute("height"));
  if (!width || !height || width > 20000 || height > 20000) throw new Error("svg has no size");
  const scale = Math.min(PNG_SCALE, PNG_MAX_WIDTH_PX / width);
  const canvasWidth = Math.max(1, Math.round(width * scale));
  const canvasHeight = Math.max(1, Math.round(height * scale));
  svg.setAttribute("width", String(canvasWidth));
  svg.setAttribute("height", String(canvasHeight));
  // max-width 样式会约束独立 SVG 文档的渲染宽度，光栅化前移除
  svg.removeAttribute("style");
  const serialized = new XMLSerializer().serializeToString(svg);
  const image = new Image();
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error("svg raster failed"));
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(serialized);
  });
  const canvas = document.createElement("canvas");
  canvas.width = canvasWidth;
  canvas.height = canvasHeight;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas unavailable");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvasWidth, canvasHeight);
  ctx.drawImage(image, 0, 0, canvasWidth, canvasHeight);
  return canvas.toDataURL("image/png");
}

export interface ChartInsertPayload {
  /** mermaid 块替换为 ![图 N](bjt-chart://N) 后的 Markdown */
  content: string;
  /** bjt-chart://N → PNG dataURL，随桥消息发给插件 */
  images: Record<string, string>;
  figureCount: number;
  failedCount: number;
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("image load failed"));
    image.src = src;
  });
}

/** 在 gantt 图 PNG 顶部自绘标题标头，返回合成后的 dataURL。
 *
 * gantt 的 title 行在渲染前已被 prepareGanttForRender 剥掉（mermaid 画布按图宽
 * 计算，超宽中文标题会被裁在画布右缘），标题改由此处绘制：字号随图宽自适应、
 * 画布宽度取 max(图宽, 标题宽)，保证标头完整可见（2026-09-07 真机反馈
 * "甘特图标头被截取、无法看到"）。失败时原样返回原图，不阻断写入。 */
async function composeGanttHeader(chartDataUrl: string, title: string): Promise<string> {
  const chart = await loadImage(chartDataUrl);
  if (!chart.width || !chart.height) return chartDataUrl;
  const fontSize = Math.min(44, Math.max(24, Math.round(chart.width / 40)));
  const font = `bold ${fontSize}px "Microsoft YaHei", "PingFang SC", Arial, sans-serif`;
  const probe = document.createElement("canvas").getContext("2d");
  if (!probe) return chartDataUrl;
  probe.font = font;
  const pad = fontSize;
  const titleWidth = probe.measureText(title).width;
  const width = Math.max(chart.width, Math.ceil(titleWidth + pad * 2));
  const headerHeight = Math.round(fontSize * 2);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = chart.height + headerHeight;
  const ctx = canvas.getContext("2d");
  if (!ctx) return chartDataUrl;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#222222";
  ctx.font = font;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(title, canvas.width / 2, headerHeight / 2, canvas.width - pad * 2);
  ctx.drawImage(chart, Math.round((canvas.width - chart.width) / 2), headerHeight);
  return canvas.toDataURL("image/png");
}

/**
 * 写入 Word 前的图表预处理：渲染全部 mermaid 块并替换为图片引用。
 * 渲染失败的块保持 ```mermaid 原样（插件按代码块文本降级插入），不阻断写入。
 */
export async function prepareChartAssets(markdown: string): Promise<ChartInsertPayload> {
  const images: Record<string, string> = {};
  const parts: string[] = [];
  let figureNo = 0;
  let failed = 0;
  for (const segment of splitMermaidFences(markdown)) {
    if (segment.kind === "text" || !segment.content.trim()) {
      parts.push(segment.content);
      continue;
    }
    const svg = await renderMermaidSvg(segment.content);
    let dataUrl = svg ? await svgToPngDataUrl(svg).catch(() => null) : null;
    const title = mermaidTitle(segment.content);
    if (dataUrl && title && /^gantt\b/.test(segment.content.trim())) {
      dataUrl = await composeGanttHeader(dataUrl, title).catch(() => dataUrl);
    }
    if (!dataUrl) {
      failed += 1;
      parts.push("```mermaid\n" + segment.content.trim() + "\n```");
      continue;
    }
    figureNo += 1;
    const key = `bjt-chart://${figureNo}`;
    images[key] = dataUrl;
    const caption = title ? `图 ${figureNo} ${title}` : `图 ${figureNo}`;
    parts.push(`![${caption}](${key})`);
  }
  return { content: parts.join("\n"), images, figureCount: figureNo, failedCount: failed };
}
