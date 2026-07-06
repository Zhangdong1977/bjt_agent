<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from "vue";
import { announcementApi } from "@/api/client";
import type { PublicAnnouncement, AnnouncementSeverity } from "@/types";

/** 公告跑马灯：拉取公开公告横向滚动展示。无公告时整条隐藏。
 *
 * 两种定价态（variant）：
 * - ``overlay``（默认）：``position: absolute`` 贴在定位父容器顶部，登录页使用；
 * - ``bar``：``position: fixed`` 固定为视口顶栏，登录后主布局使用，并通过
 *   ``v-model:visible`` 把「是否有公告」通知父组件，以便父组件相应下移顶部留白。
 *
 * 单份内容用 Web Animations API 驱动：从视口右缘滑入、左缘滑出，循环往复。
 * 全程只渲染一份内容 —— 短公告不会「同一条并排两份」；滑出瞬间即从右缘重新滑入，
 * 无空窗、无跳跃。视口/内容尺寸变化时自动重算距离与时长。
 *
 * 准实时刷新：组件挂载后每 20s 重新拉取一次（管理员改动 20s 内反映到跑马灯）；
 * 标签页隐藏时暂停轮询，重新可见时立即拉一次再恢复。请求失败保留上次内容；
 * 轮询返回内容未变时跳过重建，避免滚动跑到一半被打断重置回起点。 */
const props = withDefaults(
  defineProps<{
    variant?: "overlay" | "bar";
    visible?: boolean;
  }>(),
  { variant: "overlay", visible: false },
);
const emit = defineEmits<{ "update:visible": [boolean] }>();

const announcements = ref<PublicAnnouncement[]>([]);

// 公告有无变化时通知父组件，供其按需调整布局（如顶部留白偏移）
watch(
  () => announcements.value.length,
  (n) => emit("update:visible", n > 0),
);

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

/** 准实时刷新：每 20s 重新拉取一次，管理员改动在 20s 内反映到跑马灯。 */
const POLL_INTERVAL = 20_000;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let loading = false;

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

/** 比较两份公告的跑马灯展示内容是否一致（顺序 + id/标题/正文/严重度）。
 * 用于轮询时跳过无变化的重建，避免滚动跑到一半被打断重置。 */
function sameMarqueeItems(
  a: PublicAnnouncement[],
  b: PublicAnnouncement[],
): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (
      x.id !== y.id ||
      x.title !== y.title ||
      x.content !== y.content ||
      x.severity !== y.severity
    ) {
      return false;
    }
  }
  return true;
}

async function load() {
  if (loading) return; // 避免轮询与手动触发（如可见性回调）重叠并发
  loading = true;
  let changed = false;
  try {
    const items = await announcementApi.getPublic(20);
    if (!sameMarqueeItems(announcements.value, items)) {
      // 内容有变化才赋值（触发重渲染）并标记需要重建动画
      announcements.value = items;
      changed = true;
    }
    // 内容未变：announcements.value 引用不变，Vue 不重渲染，当前滚动继续
  } catch {
    /* 失败保留上次内容：首次失败时仍为初始 []（跑马灯隐藏），
     * 后续轮询失败不清空，避免网络抖动导致跑马灯闪烁消失。 */
  } finally {
    loading = false;
    if (changed) {
      await nextTick();
      rebuildAnimation();
    }
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

/** 启动/停止轮询。 */
function startPolling() {
  stopPolling();
  pollTimer = setInterval(load, POLL_INTERVAL);
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

/** 标签页隐藏时暂停轮询省请求；重新可见时立即拉一次并恢复轮询。 */
function onVisibilityChange() {
  if (typeof document === "undefined") return;
  if (document.hidden) {
    stopPolling();
  } else {
    load();
    startPolling();
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
  startPolling();
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }
});

onBeforeUnmount(() => {
  animation?.cancel();
  animation = null;
  resizeObserver?.disconnect();
  resizeObserver = null;
  motionMq?.removeEventListener("change", onMotionChange);
  motionMq = null;
  stopPolling();
  if (typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", onVisibilityChange);
  }
});
</script>

<template>
  <div
    v-if="announcements.length"
    class="announcement-marquee"
    :class="`announcement-marquee--${props.variant}`"
    role="status"
  >
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

/* bar 定价态：登录后主布局用作 fixed 顶栏，层级高于品牌条(100)/顶栏(99) */
.announcement-marquee--bar {
  position: fixed;
  z-index: 200;
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
