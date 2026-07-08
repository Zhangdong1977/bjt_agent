import type { RouteLocationNormalized } from 'vue-router'

/**
 * 路由 meta 字段扩展，用于驱动统一面包屑组件 AppBreadcrumb。
 * 通过 interface 增量合并 vue-router 的 RouteMeta（必须用 interface，不能 type）。
 */
declare module 'vue-router' {
  interface RouteMeta {
    /** 该页在面包屑中显示的文本（叶级路由必填，否则不渲染面包屑） */
    title?: string
    /** 静态父级路由 name（一级菜单页不填） */
    parentName?: string
    /** 动态父级路由 name：依据 query/params 决定，优先级高于 parentName */
    resolveParentName?: (to: RouteLocationNormalized) => string | null | undefined
    /** 整条隐藏面包屑（用于沉浸式全屏工作台等） */
    hideBreadcrumb?: boolean
  }
}
