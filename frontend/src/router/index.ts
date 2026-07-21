import { createRouter, createWebHistory } from "vue-router";
import ConsoleView from "@/views/ConsoleView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "console",
      component: ConsoleView,
    },
    {
      path: "/:pathMatch(.*)*",
      redirect: "/",
    },
  ],
});
