<script setup lang="ts">
import { svgToDataUrl } from "@/utils/chartAssets";

/**
 * 章节预览里的 bidfig 原语图：后端已渲染的良构 SVG，直接 <img> 沙箱展示，
 * 与写入 Word 的光栅化路径同源（所见即所得）；无渲染失败分支——SVG 由
 * backend/agent/bidfig 确定性产出，非良构在出口层已被降级为占位说明。
 */
const props = defineProps<{ svg: string }>();
</script>

<template>
  <figure class="svg-figure">
    <img class="svg-img" :src="svgToDataUrl(props.svg)" alt="图表预览">
  </figure>
</template>

<style scoped>
.svg-figure{margin:8px 0;padding:10px;border:1px solid #eee;border-radius:8px;background:#fafafa;text-align:center}
.svg-img{max-width:100%;height:auto}
</style>
