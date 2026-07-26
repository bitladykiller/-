<script setup lang="ts">
import { computed, markRaw, onMounted, onUnmounted, ref } from "vue";
import { storeToRefs } from "pinia";
import { useRouter } from "vue-router";
import {
  Activity,
  ArrowRight,
  ChartNoAxesColumnIncreasing,
  CircleAlert,
  CircleCheckBig,
  Clock3,
  Database,
  LayoutDashboard,
  MessageSquare,
  Sparkles,
  Upload,
  Users,
  Workflow,
} from "@lucide/vue";
import SidebarPanel from "@/components/SidebarPanel.vue";
import UploadDrawer from "@/components/UploadDrawer.vue";
import { useChatStore } from "@/stores/chat";
import { bindInteractiveSurfaces } from "@/utils/interaction";
import { formatTime } from "@/utils/format";
import type { ConversationSummary } from "@/api/types";

const store = useChatStore();
const router = useRouter();
const { healthy, conversations, conversationId, messages } = storeToRefs(store);

const dashboardRoot = ref<HTMLElement | null>(null);
const uploadOpen = ref(false);

const iconSet = {
  activity: markRaw(Activity),
  chart: markRaw(ChartNoAxesColumnIncreasing),
  database: markRaw(Database),
  dashboard: markRaw(LayoutDashboard),
  message: markRaw(MessageSquare),
  sparkles: markRaw(Sparkles),
  users: markRaw(Users),
  workflow: markRaw(Workflow),
};

const trendSeed = [28, 34, 39, 36, 48, 54, 58, 66];
const labels = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00"];

function buildTrend(points: number[]) {
  const width = 620;
  const height = 220;
  const padding = 16;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = Math.max(max - min, 1);

  const coords = points.map((point, index) => {
    const x = (index / (points.length - 1)) * width;
    const y = height - ((point - min) / span) * (height - padding * 2) - padding;
    return {
      x: Number(x.toFixed(2)),
      y: Number(y.toFixed(2)),
      raw: point,
    };
  });

  const line = coords
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
    .join(" ");
  const area = `${line} L ${width} ${height + 14} L 0 ${height + 14} Z`;

  return { line, area, coords, width, height };
}

const greeting = computed(() => {
  const hour = new Date().getHours();
  if (hour < 12) return "上午运营概览";
  if (hour < 18) return "下午运营概览";
  return "晚间运营概览";
});

const currentDate = computed(() =>
  new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date()),
);

const healthLabel = computed(() => {
  if (healthy.value === true) return "正常";
  if (healthy.value === false) return "异常";
  return "检查中";
});

const summaryCards = computed(() => [
  {
    label: "服务状态",
    value: healthLabel.value,
    detail:
      healthy.value === true
        ? "智能客服在线，可正常接待"
        : healthy.value === false
          ? "服务暂时异常，请稍后重试"
          : "正在确认服务是否可用",
    tone: healthy.value === true ? "ok" : healthy.value === false ? "bad" : "neutral",
    icon: iconSet.activity,
  },
  {
    label: "历史对话",
    value: String(conversations.value.length).padStart(2, "0"),
    detail: "当前账号下的会话数量",
    tone: "brand",
    icon: iconSet.users,
  },
  {
    label: "当前对话",
    value: conversationId.value ? "进行中" : "未开始",
    detail: conversationId.value ? `对话 #${conversationId.value}` : "还没有选中的对话",
    tone: conversationId.value ? "brand" : "neutral",
    icon: iconSet.workflow,
  },
  {
    label: "本轮消息",
    value: String(messages.value.length).padStart(2, "0"),
    detail: messages.value.length ? "用户页已有对话内容" : "等待新的用户提问",
    tone: "neutral",
    icon: iconSet.message,
  },
]);

const trend = computed(() =>
  buildTrend(
    healthy.value === false ? trendSeed.map((point, index) => point - (index > 4 ? 10 : 6)) : trendSeed,
  ),
);

const modules = [
  {
    name: "智能理解",
    badge: "问答",
    desc: "自动判断用户是在咨询商品、查询订单，还是咨询售后政策。",
    icon: iconSet.sparkles,
  },
  {
    name: "资料查询",
    badge: "知识",
    desc: "结合产品说明、常见问题与政策文档，给出更靠谱的回答。",
    icon: iconSet.database,
  },
  {
    name: "连续对话",
    badge: "记忆",
    desc: "同一会话会记住上下文，用户不用反复说明背景。",
    icon: iconSet.workflow,
  },
  {
    name: "运营总览",
    badge: "管理",
    desc: "集中查看服务状态、会话活跃度和资料处理情况。",
    icon: iconSet.dashboard,
  },
];

const tasks = computed(() => [
  {
    title: "资料更新",
    owner: "资料库",
    eta: healthy.value === true ? "进行中" : "待恢复",
    status: healthy.value === true ? "running" : "blocked",
    note: "新上传的文档会自动处理，完成后即可用于回答。",
  },
  {
    title: "会话整理",
    owner: "对话记录",
    eta: conversationId.value ? `对话 #${conversationId.value}` : "等待新对话",
    status: conversationId.value ? "ready" : "queued",
    note: "当前对话标题与上下文会持续保存，方便用户继续追问。",
  },
  {
    title: "知识巡检",
    owner: "知识图谱",
    eta: "本日巡检",
    status: "ready",
    note: "检查商品关系与政策条目是否完整，保障回答质量。",
  },
  {
    title: "服务巡检",
    owner: "系统运维",
    eta: "正常运行",
    status: "running",
    note: "持续关注接口可用性与会话处理是否顺畅。",
  },
]);

const servicePanels = computed(() => [
  {
    name: "客服接口",
    desc: "问答与实时回复",
    state: healthy.value === true ? "healthy" : healthy.value === false ? "degraded" : "checking",
    stat: healthy.value === true ? "92 ms" : "--",
  },
  {
    name: "资料检索",
    desc: "文档与向量检索",
    state: healthy.value === true ? "healthy" : healthy.value === false ? "degraded" : "checking",
    stat: healthy.value === true ? "34 ms" : "--",
  },
  {
    name: "关系知识",
    desc: "商品与政策关联",
    state: healthy.value === true ? "healthy" : healthy.value === false ? "degraded" : "checking",
    stat: healthy.value === true ? "41 ms" : "--",
  },
  {
    name: "会话存储",
    desc: "缓存与历史记录",
    state: healthy.value === true ? "healthy" : healthy.value === false ? "degraded" : "checking",
    stat: healthy.value === true ? "8 ms" : "--",
  },
]);

const recentConversations = computed(() => conversations.value.slice(0, 4));

let healthTimer: number | undefined;
let stopInteractive: (() => void) | null = null;

function openUserWorkspace() {
  void router.push({ name: "user" });
}

function startNewChat() {
  store.newChat();
  void router.push({ name: "user" });
}

async function openConversation(item: ConversationSummary) {
  store.selectConversation(item);
  await router.push({ name: "user" });
}

function taskStatusLabel(status: string) {
  if (status === "ready") return "已就绪";
  if (status === "running") return "进行中";
  if (status === "blocked") return "阻塞";
  return "排队中";
}

onMounted(async () => {
  await store.refreshHealth();
  await store.refreshConversations();
  healthTimer = window.setInterval(() => {
    void store.refreshHealth();
  }, 30000);
  if (dashboardRoot.value) {
    stopInteractive = bindInteractiveSurfaces(dashboardRoot.value);
  }
});

onUnmounted(() => {
  if (healthTimer) {
    window.clearInterval(healthTimer);
  }
  stopInteractive?.();
});
</script>

<template>
  <div ref="dashboardRoot" class="admin-page">
    <SidebarPanel class="admin-side" />

    <section class="admin-stage">
      <header class="admin-topbar reveal">
        <div class="top-copy">
          <div class="top-mark">
            <LayoutDashboard :size="18" />
          </div>
          <div>
            <p class="eyebrow">运营后台</p>
            <h1>{{ greeting }}</h1>
            <p class="lede">
              在这里查看服务状态、对话活跃度和资料处理情况，方便日常运维与客服支持。
            </p>
          </div>
        </div>

        <div class="top-actions">
          <div class="top-chip">
            <Clock3 :size="14" />
            <span>{{ currentDate }}</span>
          </div>
          <button type="button" class="ghost-btn" @click="uploadOpen = true">
            <Upload :size="15" />
            <span>管理资料</span>
          </button>
          <button type="button" class="ghost-btn" @click="openUserWorkspace">
            <ArrowRight :size="15" />
            <span>进入客服页</span>
          </button>
          <button type="button" class="primary-btn" @click="startNewChat">
            <MessageSquare :size="15" />
            <span>新建对话</span>
          </button>
        </div>
      </header>

      <section class="summary-strip reveal reveal-delay-1">
        <article
          v-for="(card, index) in summaryCards"
          :key="card.label"
          class="summary-card tilt-card"
          :class="card.tone"
          data-tilt
          :style="{ animationDelay: `${index * 50}ms` }"
        >
          <span class="summary-icon">
            <component :is="card.icon" :size="16" />
          </span>
          <div>
            <small>{{ card.label }}</small>
            <strong>{{ card.value }}</strong>
            <p>{{ card.detail }}</p>
          </div>
        </article>
      </section>

      <div class="dashboard-grid reveal reveal-delay-2">
        <section class="hero-surface">
          <div class="hero-copy">
            <div class="hero-head">
              <p class="eyebrow">运营总览</p>
              <span class="hero-pill">
                <Sparkles :size="14" />
                <span>{{ healthy === true ? "服务运行正常" : "建议先检查服务状态" }}</span>
              </span>
            </div>
            <h2>智能客服运营总览</h2>
            <p>
              集中查看对话情况、资料更新和服务健康状态，便于快速了解当前客服系统是否正常运转。
            </p>
          </div>

          <div class="chart-panel">
            <div class="chart-head">
              <div>
                <p class="panel-label">今日趋势</p>
                <strong>咨询与回复量</strong>
              </div>
              <span class="chart-chip">
                <ChartNoAxesColumnIncreasing :size="14" />
                <span>今日概览</span>
              </span>
            </div>

            <svg class="trend" :viewBox="`0 0 ${trend.width} ${trend.height + 14}`" aria-hidden="true">
              <defs>
                <linearGradient id="trendArea" x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" stop-color="rgba(15,159,143,0.24)" />
                  <stop offset="100%" stop-color="rgba(15,159,143,0.02)" />
                </linearGradient>
              </defs>
              <path :d="trend.area" fill="url(#trendArea)" />
              <path :d="trend.line" class="line" />
              <g v-for="point in trend.coords" :key="`${point.x}-${point.y}`">
                <circle :cx="point.x" :cy="point.y" r="4" class="dot" />
              </g>
            </svg>

            <div class="chart-labels">
              <span v-for="label in labels" :key="label">{{ label }}</span>
            </div>
          </div>
        </section>

        <aside class="services-surface">
          <div class="surface-head">
            <p class="panel-label">服务健康</p>
            <span class="surface-pill" :class="{ ok: healthy === true, bad: healthy === false }">
              <component
                :is="healthy === true ? CircleCheckBig : healthy === false ? CircleAlert : Activity"
                :size="14"
              />
              <span>{{ healthLabel }}</span>
            </span>
          </div>
          <div class="service-list">
            <article v-for="service in servicePanels" :key="service.name" class="service-item">
              <div>
                <strong>{{ service.name }}</strong>
                <span>{{ service.desc }}</span>
              </div>
              <div class="service-meta">
                <em :class="service.state">{{ service.state }}</em>
                <b>{{ service.stat }}</b>
              </div>
            </article>
          </div>
        </aside>

        <section class="modules-surface">
          <div class="surface-head">
            <p class="panel-label">能力模块</p>
            <span class="surface-pill neutral">4 项能力</span>
          </div>
          <div class="module-grid">
            <article v-for="module in modules" :key="module.name" class="module-card">
              <span class="module-icon">
                <component :is="module.icon" :size="16" />
              </span>
              <div class="module-body">
                <div class="module-top">
                  <strong>{{ module.name }}</strong>
                  <em>{{ module.badge }}</em>
                </div>
                <p>{{ module.desc }}</p>
              </div>
            </article>
          </div>
        </section>

        <section class="tasks-surface">
          <div class="surface-head">
            <p class="panel-label">当前任务</p>
            <span class="surface-pill brand">处理队列</span>
          </div>
          <div class="task-list">
            <article v-for="task in tasks" :key="task.title" class="task-item">
              <div class="task-main">
                <strong>{{ task.title }}</strong>
                <span>{{ task.note }}</span>
              </div>
              <div class="task-side">
                <em>{{ task.owner }}</em>
                <b>{{ task.eta }}</b>
                <span class="task-state" :class="task.status">{{ taskStatusLabel(task.status) }}</span>
              </div>
            </article>
          </div>
        </section>

        <section class="recent-surface">
          <div class="surface-head">
            <p class="panel-label">最近会话</p>
            <span class="surface-pill neutral">{{ recentConversations.length }} 条</span>
          </div>
          <div class="recent-list">
            <button
              v-for="item in recentConversations"
              :key="item.id"
              type="button"
              class="recent-item"
              @click="openConversation(item)"
            >
              <div>
                <strong>{{ item.title }}</strong>
                <span>#{{ item.id }} · {{ formatTime(item.created_at) }}</span>
              </div>
              <ArrowRight :size="15" />
            </button>
            <p v-if="!recentConversations.length" class="empty-note">
              当前还没有历史会话，先到用户页发送第一条消息。
            </p>
          </div>
        </section>
      </div>

      <UploadDrawer v-model:open="uploadOpen" />
    </section>
  </div>
</template>

<style scoped>
.admin-page {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  height: 100%;
  max-width: 1680px;
  margin: 0 auto;
  overflow: hidden;
}

.admin-side,
.admin-stage {
  min-height: 0;
  position: relative;
  z-index: 1;
}

.admin-stage {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 22px 22px;
  min-width: 0;
  overflow: auto;
}

.admin-topbar,
.top-copy,
.top-actions,
.hero-head,
.service-item,
.service-meta,
.module-top,
.task-item,
.task-side,
.recent-item {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.admin-topbar {
  justify-content: space-between;
  align-items: flex-start;
}

.top-copy {
  min-width: 0;
  align-items: flex-start;
  gap: 14px;
}

.top-mark {
  width: 46px;
  height: 46px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  border-radius: 16px;
  color: #fff;
  background: linear-gradient(145deg, var(--brand), var(--iris));
  box-shadow: var(--shadow-glow);
}

.eyebrow,
.panel-label {
  margin: 0;
  font-size: 0.7rem;
  line-height: 1.2;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--text-soft);
  font-weight: 600;
}

.top-copy h1 {
  margin: 8px 0 10px;
  font-size: clamp(1.85rem, 2.8vw, 2.65rem);
  line-height: 1.05;
  letter-spacing: -0.03em;
}

.lede,
.hero-copy p,
.summary-card p,
.service-item span,
.module-card p,
.task-main span,
.recent-item span,
.empty-note {
  color: var(--text-muted);
  line-height: 1.65;
}

.lede {
  margin: 0;
  max-width: 64ch;
}

.top-actions {
  justify-content: flex-end;
}

.top-chip,
.ghost-btn,
.primary-btn,
.hero-pill,
.chart-chip,
.surface-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 9px 13px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.8);
  color: var(--text-muted);
  font-size: 0.82rem;
  backdrop-filter: blur(10px);
  transition:
    transform 160ms var(--ease-out),
    box-shadow 160ms ease,
    border-color 160ms ease;
}

.ghost-btn:hover,
.primary-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

.primary-btn {
  border-color: transparent;
  background: linear-gradient(135deg, var(--brand), var(--brand-strong) 55%, var(--brand-deep));
  color: #fff;
  box-shadow: var(--shadow-glow);
  font-weight: 600;
}

.summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.summary-card,
.hero-surface,
.services-surface,
.modules-surface,
.tasks-surface,
.recent-surface {
  border: 1px solid rgba(255, 255, 255, 0.55);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.78);
  box-shadow: var(--shadow-md);
  backdrop-filter: blur(16px);
}

.summary-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 15px;
  animation: rise-in 0.5s var(--ease-out) both;
}

.summary-icon,
.module-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  border-radius: 14px;
  color: var(--brand-strong);
  background: linear-gradient(145deg, rgba(11, 154, 136, 0.14), rgba(79, 86, 239, 0.08));
  border: 1px solid rgba(11, 154, 136, 0.1);
}

.summary-card strong,
.service-item strong,
.module-card strong,
.task-main strong,
.recent-item strong,
.hero-copy h2,
.chart-head strong {
  display: block;
}

.summary-card small {
  display: block;
  color: var(--text-soft);
  font-size: 0.74rem;
}

.summary-card strong {
  margin: 3px 0 4px;
  font-size: 1.12rem;
  letter-spacing: -0.02em;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) 320px;
  grid-template-areas:
    "hero services"
    "modules tasks"
    "modules recent";
  gap: 16px;
  flex: 1;
  min-height: 0;
}

.hero-surface {
  grid-area: hero;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  gap: 18px;
  padding: 20px;
  background:
    linear-gradient(145deg, rgba(11, 154, 136, 0.12), rgba(79, 86, 239, 0.06) 55%, rgba(227, 154, 45, 0.07)),
    rgba(255, 255, 255, 0.78);
  position: relative;
  overflow: hidden;
}

.hero-copy h2 {
  margin: 12px 0 12px;
  font-size: clamp(1.55rem, 2.1vw, 2.1rem);
  line-height: 1.12;
  max-width: 16ch;
  letter-spacing: -0.03em;
}

.hero-copy p {
  margin: 0;
  max-width: 56ch;
}

.chart-panel,
.services-surface,
.modules-surface,
.tasks-surface,
.recent-surface {
  padding: 16px;
}

.chart-panel {
  display: grid;
  gap: 14px;
  border: 1px solid rgba(24, 42, 34, 0.08);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.78);
  box-shadow: var(--shadow-sm);
}

.chart-head,
.surface-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.trend {
  width: 100%;
  height: auto;
}

.line {
  fill: none;
  stroke: var(--brand);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.dot {
  fill: #fff;
  stroke: var(--brand);
  stroke-width: 2.5;
}

.chart-labels {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 8px;
  color: var(--text-soft);
  font-size: 0.74rem;
}

.services-surface {
  grid-area: services;
  display: grid;
  gap: 14px;
  align-content: start;
}

.surface-pill.ok {
  color: var(--success);
  border-color: rgba(45, 157, 96, 0.16);
  background: rgba(45, 157, 96, 0.08);
}

.surface-pill.bad {
  color: var(--danger);
  border-color: rgba(216, 95, 110, 0.16);
  background: rgba(216, 95, 110, 0.08);
}

.surface-pill.brand {
  color: var(--brand-strong);
  border-color: rgba(15, 159, 143, 0.16);
  background: rgba(15, 159, 143, 0.08);
}

.surface-pill.neutral {
  background: rgba(255, 255, 255, 0.68);
}

.service-list,
.task-list,
.recent-list {
  display: grid;
  gap: 10px;
}

.service-item,
.module-card,
.task-item,
.recent-item {
  justify-content: space-between;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: var(--shadow-sm);
  position: relative;
  z-index: 0;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease,
    background 160ms ease;
}

.service-item:hover,
.module-card:hover,
.task-item:hover,
.recent-item:hover {
  z-index: 1;
  border-color: rgba(11, 154, 136, 0.18);
  box-shadow: var(--shadow-md);
  background: rgba(255, 255, 255, 0.98);
}

.service-item,
.task-item,
.recent-item {
  padding: 14px;
}

.service-item span,
.recent-item span,
.task-main span {
  display: block;
  font-size: 0.82rem;
}

.service-meta {
  align-items: end;
  flex-direction: column;
  min-width: fit-content;
}

.service-meta em,
.module-top em,
.task-state {
  font-style: normal;
  font-size: 0.74rem;
  color: var(--text-soft);
}

.service-meta b,
.task-side b {
  font-size: 0.84rem;
}

.service-meta em.healthy {
  color: var(--success);
}

.service-meta em.degraded {
  color: var(--danger);
}

.modules-surface {
  grid-area: modules;
  display: grid;
  gap: 14px;
  align-content: start;
}

.module-grid {
  display: grid;
  gap: 10px;
}

.module-card {
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  gap: 12px;
  padding: 14px;
  text-align: left;
  width: 100%;
}

.module-body {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 6px;
  text-align: left;
  justify-items: start;
}

.module-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  text-align: left;
}

.module-top strong {
  display: block;
  text-align: left;
  min-width: 0;
  flex: 1;
}

.module-top em {
  flex-shrink: 0;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(24, 42, 34, 0.06);
}

.module-card p {
  margin: 0;
  width: 100%;
  text-align: left;
  font-size: 0.82rem;
  line-height: 1.55;
}

.tasks-surface {
  grid-area: tasks;
  display: grid;
  gap: 14px;
  align-content: start;
}

.task-main {
  flex: 1;
  min-width: 0;
}

.task-side {
  align-items: end;
  flex-direction: column;
  min-width: fit-content;
}

.task-side em {
  color: var(--text-soft);
}

.task-state.ready {
  color: var(--success);
}

.task-state.running {
  color: var(--brand-strong);
}

.task-state.blocked {
  color: var(--danger);
}

.recent-surface {
  grid-area: recent;
  display: grid;
  gap: 14px;
  align-content: start;
}

.recent-item {
  text-align: left;
}

.recent-item svg {
  flex-shrink: 0;
  color: var(--text-soft);
}

.empty-note {
  margin: 0;
  font-size: 0.82rem;
}

@media (max-width: 1240px) {
  .summary-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dashboard-grid {
    grid-template-columns: 1fr;
    grid-template-areas:
      "hero"
      "services"
      "modules"
      "tasks"
      "recent";
  }

  .hero-surface {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .admin-page {
    grid-template-columns: 1fr;
  }

  .admin-stage {
    padding: 18px;
  }
}

@media (max-width: 640px) {
  .admin-stage {
    padding: 14px;
  }

  .summary-strip {
    grid-template-columns: 1fr;
  }

  .hero-surface,
  .chart-panel,
  .services-surface,
  .modules-surface,
  .tasks-surface,
  .recent-surface {
    padding: 14px;
  }

  .top-actions {
    justify-content: stretch;
  }

  .ghost-btn,
  .primary-btn,
  .top-chip {
    width: 100%;
    justify-content: center;
  }

  .chart-labels {
    grid-template-columns: repeat(4, 1fr);
  }
}
</style>
