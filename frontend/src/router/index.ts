import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/LoginView.vue"),
      meta: { guest: true },
    },
    {
      path: "/",
      redirect: "/home/check",
    },
    {
      path: "/home",
      name: "home",
      redirect: "/home/check",
      component: () => import("@/components/AppLayout.vue"),
      meta: { requiresAuth: true },
      children: [
        {
          path: "",
          name: "home-index",
          redirect: "/home/check",
        },
        {
          path: "check",
          name: "check",
          component: () => import("@/views/CheckView.vue"),
          meta: { title: "标书检查" },
        },
        {
          path: "history",
          name: "history",
          component: () => import("@/views/HistoryView.vue"),
          meta: { title: "历史标书" },
        },
        {
          path: "knowledge",
          name: "knowledge",
          component: () => import("@/views/KnowledgeView.vue"),
          meta: { title: "知识库" },
        },
        {
          path: "profile",
          name: "profile-center",
          component: () => import("@/views/ProfileCenterView.vue"),
          meta: { title: "用户中心" },
        },
        {
          path: "projects/:id/review",
          name: "review-timeline",
          component: () => import("@/views/ReviewTimelineView.vue"),
          meta: { title: "审查时间线", parentName: "history", interiorOnly: true },
        },
        {
          path: "projects/:id/review-execution",
          name: "review-execution",
          component: () => import("@/views/ReviewExecutionView.vue"),
          meta: { hideBreadcrumb: true },
        },
        {
          path: "projects/:id/results",
          name: "review-results",
          component: () => import("@/views/ResultsView.vue"),
          meta: {
            title: "审查结果",
            resolveParentName: (to) =>
              to.query.from === "experience" ? "experience-dashboard" : "history",
          },
        },
        {
          path: "experience",
          name: "experience-dashboard",
          component: () => import("@/views/ExperienceDashboard.vue"),
          meta: { title: "标书复盘", interiorOnly: true },
        },
        {
          path: "announcements",
          name: "announcement-manage",
          component: () => import("@/views/AnnouncementManageView.vue"),
          meta: { title: "系统公告", interiorOnly: true },
        },
        {
          path: "system-status",
          name: "system-status",
          component: () => import("@/views/SystemStatusView.vue"),
          meta: { title: "系统状态", interiorOnly: true },
        },
      ],
    },
  ],
});

router.beforeEach(async (to, _from, next) => {
  const authStore = useAuthStore();

  if (!authStore.initialized) {
    await authStore.initialize();
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    next({ name: "login" });
  } else if (to.meta.guest && authStore.isAuthenticated) {
    next("/home/check");
  } else if (to.meta.interiorOnly && !authStore.isInteriorUser) {
    next("/home/check");
  } else {
    next();
  }
});

export default router;
