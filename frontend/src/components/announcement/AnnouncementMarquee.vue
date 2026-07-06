<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from "vue";
import { announcementApi } from "@/api/client";
import type { PublicAnnouncement, AnnouncementSeverity } from "@/types";

/** 登录页跑马灯：拉取公开公告横向滚动展示。无公告时整条隐藏。
 *
 * 单份内容用 Web Animations API 驱动：从视口右缘滑入、左缘滑出，循环往复。
 * 全程只渲染一份内容 —— 短公告不会「同一条并排两份」；滑出瞬间即从右缘重新滑入，
 * 无空窗、无跳跃。视口/内容尺寸变化时自动重算距离与时长。 */
const announcements = ref<PublicAnnouncement[]>([]);

const items = computed(() =>
  announcements.value.map((a) => ({
    id: a.id,
    severity: a.severity,
    text: a.content ? `${a.title}　${a.content}` : a.title,
  })),
);

const viewportEl = ref<HTMLDivElement | null>(null);
const trackEl = ref<HTMLDivElement | null>(null);

const severityClass = (s: AnnouncementSeverity) => `marquee-item--${s}`;

let animation: Animation | null = null;
let resizeObserver: ResizeObserver | null = null;
let motionMq: MediaQueryList | null = null;

const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  !!window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** （重新）构建滚动动画。距离 = 视口宽 + 内容宽，约 100px/s（下限 5s）。 */
function rebuildAnimation() {
  const vp = viewportEl.value;
  const tr = trackEl.value;
  if (!vp || !tr) return;
  animation?.cancel();
  animation = null;
  if (prefersReducedMotion()) return; // 尊重「减少动态」：静态展示
  const vpW = vp.clientWidth;
  const contentW = tr.scrollWidth;
  if (vpW === 0 || contentW === 0) return;
  const duration = Math.max(5000, Math.round((vpW + contentW) * 10));
  animation = tr.animate(
    [
      { transform: `translateX(${vpW}px)` }, // 内容左缘齐视口右缘，即将滑入
      { transform: `translateX(${-contentW}px)` }, // 内容右缘齐视口左缘，刚好滑出
    ],
    { duration, iterations: Infinity, easing: "linear" },
  );
}

async function load() {
  try {
    announcements.value = await announcementApi.getPublic(20);
  } catch {
    announcements.value = [];
  } finally {
    await nextTick();
    rebuildAnimation();
  }
}

function onMotionChange(e: MediaQueryListEvent) {
  if (e.matches) {
    animation?.cancel();
    animation = null;
  } else {
    rebuildAnimation();
  }
}

onMounted(async () => {
  await load();
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => rebuildAnimation());
    if (viewportEl.value) resizeObserver.observe(viewportEl.value);
    if (trackEl.value) resizeObserver.observe(trackEl.value);
  }
  if (typeof window !== "undefined" && window.matchMedia) {
    motionMq = window.matchMedia("(prefers-reduced-motion: reduce)");
    motionMq.addEventListener("change", onMotionChange);
  }
});

onBeforeUnmount(() => {
  animation?.cancel();
  animation = null;
  resizeObserver?.disconnect();
  resizeObserver = null;
  motionMq?.removeEventListener("change", onMotionChange);
  motionMq = null;
});
</script>

<template>
  <div v-if="announcements.length" class="announcement-marquee" role="status">
    <span class="marquee-label">
      <span class="marquee-icon">📢</span>
      系统公告
    </span>
    <div ref="viewportEl" class="marquee-viewport">
      <div ref="trackEl" class="marquee-track">
        <template v-for="(item, i) in items" :key="item.id">
          <span v-if="i > 0" class="marquee-sep">·</span>
          <span class="marquee-item" :class="severityClass(item.severity)">
            <span class="marquee-dot"></span>
            {{ item.text }}
          </span>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.announcement-marquee {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 32px;
  z-index: 5;
  display: flex;
  align-items: stretch;
  background: #fff;
  border-bottom: 1px solid #f0e6e7;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.04);
  overflow: hidden;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

.marquee-label {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 14px;
  background: linear-gradient(90deg, #d7041a 0%, #b80015 100%);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.marquee-icon {
  font-size: 13px;
  line-height: 1;
}

.marquee-viewport {
  flex: 1 1 auto;
  overflow: hidden;
  display: flex;
  align-items: center;
  mask-image: linear-gradient(
    90deg,
    transparent 0,
    #000 24px,
    #000 calc(100% - 24px),
    transparent 100%
  );
}

.marquee-track {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}

.marquee-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 22px;
  font-size: 13px;
  color: #444;
}

.marquee-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: 0 0 auto;
  background: #d9d9d9;
}

/* 严重度配色 */
.marquee-item--info .marquee-dot {
  background: #52c41a;
}
.marquee-item--important .marquee-dot {
  background: #fa8c16;
}
.marquee-item--urgent {
  color: #d7041a;
  font-weight: 600;
}
.marquee-item--urgent .marquee-dot {
  background: #d7041a;
}

.marquee-sep {
  color: #ddd;
  padding: 0 4px;
}
</style>
