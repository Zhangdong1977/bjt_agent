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
        },
        {
          path: "history",
          name: "history",
          component: () => import("@/views/HistoryView.vue"),
        },
        {
          path: "knowledge",
          name: "knowledge",
          component: () => import("@/views/KnowledgeView.vue"),
        },
        {
          path: "profile",
          name: "profile-center",
          component: () => import("@/views/ProfileCenterView.vue"),
        },
        {
          path: "projects/:id/review",
          name: "review-timeline",
          component: () => import("@/views/ReviewTimelineView.vue"),
          meta: { interiorOnly: true },
        },
        {
          path: "projects/:id/review-execution",
          name: "review-execution",
          component: () => import("@/views/ReviewExecutionView.vue"),
        },
        {
          path: "projects/:id/results",
          name: "review-results",
          component: () => import("@/views/ResultsView.vue"),
        },
        {
          path: "experience",
          name: "experience-dashboard",
          component: () => import("@/views/ExperienceDashboard.vue"),
          meta: { interiorOnly: true },
        },
        {
          path: "announcements",
          name: "announcement-manage",
          component: () => import("@/views/AnnouncementManageView.vue"),
          meta: { interiorOnly: true },
        },
        {
          path: "system-status",
          name: "system-status",
          component: () => import("@/views/SystemStatusView.vue"),
          meta: { interiorOnly: true },
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
