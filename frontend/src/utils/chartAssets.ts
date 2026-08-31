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
        fontFamily: '"Microsoft YaHei", "PingFang SC", Arial, sans-serif',
        // useMaxWidth:false 让 SVG 根节点输出像素级 width/height（默认 100% 会导致
        // 光栅化时尺寸解析歧义）；htmlLabels 保持默认开启，中文标签才能正常
        // 换行与内边距（WebView2/Chromium 支持 foreignObject 光栅化）。
        flowchart: { useMaxWidth: false, curve: "basis" },
        gantt: { useMaxWidth: false },
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
    const { svg } = await mermaid.render(`bjt-chart-${renderSeq}-${Date.now().toString(36)}`, code.trim());
    return svg || null;
  } catch {
    return null;
  }
}

/** SVG 字符串 → data URL（<img> 沙箱加载，预览与光栅化共用，所见即所得）。 */
export function svgToDataUrl(svgText: string): string {
  const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
  const svg = doc.documentElement;
  if (svg && svg.nodeName.toLowerCase() === "svg") {
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

/** 仅接受纯像素数值（"800"/"800px"）；"100%" 之类的相对值返回 NaN。 */
function parsePixelLength(value: string | null): number {
  if (!value) return NaN;
  const text = value.trim();
  if (!/^\d+(?:\.\d+)?px?$/.test(text)) return NaN;
  const parsed = parseFloat(text);
  return Number.isFinite(parsed) ? parsed : NaN;
}

async function svgToPngDataUrl(svgText: string): Promise<string> {
  const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
  const svg = doc.documentElement;
  if (!svg || svg.nodeName.toLowerCase() !== "svg") throw new Error("invalid svg");
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
    const dataUrl = svg ? await svgToPngDataUrl(svg).catch(() => null) : null;
    if (!dataUrl) {
      failed += 1;
      parts.push("```mermaid\n" + segment.content.trim() + "\n```");
      continue;
    }
    figureNo += 1;
    const key = `bjt-chart://${figureNo}`;
    images[key] = dataUrl;
    const title = mermaidTitle(segment.content);
    const caption = title ? `图 ${figureNo} ${title}` : `图 ${figureNo}`;
    parts.push(`![${caption}](${key})`);
  }
  return { content: parts.join("\n"), images, figureCount: figureNo, failedCount: failed };
}
