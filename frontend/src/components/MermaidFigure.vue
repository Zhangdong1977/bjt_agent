<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { renderMermaidSvg, svgToDataUrl } from "@/utils/chartAssets";

/**
 * 章节预览弹窗里的 mermaid 图渲染。预览走 <img> + SVG data URL（浏览器
 * 沙箱，禁脚本/禁外链），与写入 Word 的光栅化路径同源，所见即所得；
 * 渲染失败时降级显示源码，提示用户该图将以代码文本形式写入 Word。
 */
const props = defineProps<{ code: string }>();

const src = ref("");
const failed = ref(false);
let renderToken = 0;

async function render() {
  const token = ++renderToken;
  failed.value = false;
  src.value = "";
  const out = await renderMermaidSvg(props.code);
  if (token !== renderToken) return;
  if (out) {
    src.value = svgToDataUrl(out);
  } else {
    failed.value = true;
  }
}

onMounted(render);
watch(() => props.code, render);
</script>

<template>
  <figure class="mermaid-figure">
    <img v-if="src" class="mermaid-img" :src="src" alt="mermaid 图表预览">
    <pre v-else-if="failed" class="mermaid-source">```mermaid
{{ code }}
```</pre>
    <div v-else class="mermaid-loading">图表渲染中…</div>
  </figure>
</template>

<style scoped>
.mermaid-figure{margin:8px 0;padding:10px;border:1px solid #eee;border-radius:8px;background:#fafafa;text-align:center}
.mermaid-img{max-width:100%;height:auto}
.mermaid-source{margin:0;text-align:left;color:#c58608;font-size:10px;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.mermaid-loading{color:#999;font-size:10px;padding:14px 0}
</style>
