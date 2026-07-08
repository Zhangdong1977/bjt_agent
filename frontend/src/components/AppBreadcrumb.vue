<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw, type RouteLocationNormalized } from 'vue-router'

/**
 * 统一面包屑：完全基于当前路由的 meta 渲染，挂在 AppLayout 内容区顶部。
 * - 不遍历 route.matched（含 AppLayout 层且无 title），改用显式 parentName / resolveParentName。
 * - 首页恒为第一项（指向 check），中间父级可点击，末项（当前页）为纯文本。
 */
interface Crumb {
  label: string
  to?: RouteLocationRaw
}

const route = useRoute()
const router = useRouter()

const HOME: Crumb = { label: '首页', to: { name: 'check' } }

const crumbs = computed<Crumb[]>(() => {
  const m = route.meta
  if (m.hideBreadcrumb || !m.title) return []

  const list: Crumb[] = [HOME]

  // 解析父级：动态函数优先，回落到静态 parentName
  const parentName = m.resolveParentName
    ? m.resolveParentName(route as RouteLocationNormalized)
    : m.parentName
  if (parentName) {
    const resolved = router.resolve({ name: parentName } as RouteLocationRaw)
    // 父级目标路由必须有 title，否则静默跳过该级
    if (resolved.meta?.title) {
      list.push({ label: resolved.meta.title, to: { name: parentName } })
    }
  }

  list.push({ label: m.title }) // 当前页，不可点
  return list
})

function handleClick(to?: RouteLocationRaw) {
  if (to) router.push(to)
}
</script>

<template>
  <a-breadcrumb v-if="crumbs.length" class="app-breadcrumb">
    <a-breadcrumb-item v-for="(c, i) in crumbs" :key="i">
      <a v-if="c.to && i < crumbs.length - 1" @click="handleClick(c.to)">{{ c.label }}</a>
      <span v-else>{{ c.label }}</span>
    </a-breadcrumb-item>
  </a-breadcrumb>
</template>

<style scoped>
.app-breadcrumb {
  margin-bottom: 16px;
}
</style>
