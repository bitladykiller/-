<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import SidebarPanel from "@/components/SidebarPanel.vue";
import MessageList from "@/components/MessageList.vue";
import UploadDrawer from "@/components/UploadDrawer.vue";
import { useChatStore } from "@/stores/chat";

const store = useChatStore();
const { messages, streaming, statusLine, error, activeTitle, conversationId } =
  storeToRefs(store);

const draft = ref("");
const uploadOpen = ref(false);
const mobileNav = ref(false);

const prompts = [
  "智能门锁有哪些型号与价格？",
  "保修政策和退换货流程是怎样的？",
  "帮我查一下订单 10248 的状态",
];

function resize(e: Event) {
  const ta = e.target as HTMLTextAreaElement;
  ta.style.height = "auto";
  ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
}

async function onSend() {
  const text = draft.value;
  draft.value = "";
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (ta) {
    ta.style.height = "auto";
  }
  mobileNav.value = false;
  await store.send(text);
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    void onSend();
  }
}

async function onDelete() {
  if (!conversationId.value) return;
  if (!confirm("删除该会话并清理关联记忆？")) return;
  await store.removeConversation(conversationId.value);
}

let healthTimer: number | undefined;

onMounted(async () => {
  await store.refreshHealth();
  await store.refreshConversations();
  healthTimer = window.setInterval(() => {
    void store.refreshHealth();
  }, 30000);
});

onUnmounted(() => {
  if (healthTimer) window.clearInterval(healthTimer);
});

watch(error, (v) => {
  if (v) {
    // keep visible in status strip
  }
});
</script>

<template>
  <div class="console" :class="{ nav: mobileNav }">
    <div class="ambient" aria-hidden="true">
      <div class="wash" />
      <div class="rail" />
    </div>

    <SidebarPanel class="side" />

    <section class="stage">
      <header class="top">
        <button type="button" class="nav-btn" @click="mobileNav = !mobileNav">
          菜单
        </button>
        <div class="titles">
          <p class="eyebrow">Smart-home concierge</p>
          <h1>{{ activeTitle }}</h1>
        </div>
        <div class="actions">
          <button type="button" class="ghost" @click="uploadOpen = true">
            知识文档
          </button>
          <button
            type="button"
            class="ghost danger"
            :disabled="!conversationId"
            @click="onDelete"
          >
            删除
          </button>
        </div>
      </header>

      <div v-if="!messages.length" class="hero">
        <div class="hero-card">
          <p class="eyebrow">AssistGen Console</p>
          <h2>
            以工程化工作台，
            <em>接通</em> 图谱与文档智能。
          </h2>
          <p class="lede">
            前后端分离部署。会话、SSE 流式回答、知识入库均对接现有 FastAPI。
            经营范围内的智能家居问题可直接提问。
          </p>
          <div class="prompt-row">
            <button
              v-for="p in prompts"
              :key="p"
              type="button"
              class="prompt"
              @click="store.send(p)"
            >
              {{ p }}
            </button>
          </div>
        </div>
      </div>
      <MessageList v-else :messages="messages" />

      <footer class="composer">
        <div class="box">
          <textarea
            id="composer-input"
            v-model="draft"
            rows="1"
            placeholder="写下你的问题 — Enter 发送，Shift+Enter 换行"
            :disabled="streaming"
            @input="resize"
            @keydown="onKey"
          />
          <button
            type="button"
            class="send"
            :disabled="streaming || !draft.trim()"
            @click="onSend"
          >
            {{ streaming ? "生成中" : "发送" }}
          </button>
        </div>
        <div class="status">
          <span>{{ statusLine }}</span>
          <span v-if="error" class="err"> · {{ error }}</span>
        </div>
      </footer>
    </section>

    <div v-if="mobileNav" class="scrim" @click="mobileNav = false" />
    <UploadDrawer v-model:open="uploadOpen" />
  </div>
</template>

<style scoped>
.console {
  position: relative;
  display: grid;
  grid-template-columns: 300px 1fr;
  height: 100%;
  max-width: 1480px;
  margin: 0 auto;
}

.ambient {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
}

.wash {
  position: absolute;
  width: 55vw;
  height: 55vw;
  right: -10vw;
  top: -20vw;
  background: radial-gradient(circle, rgba(232, 160, 84, 0.14), transparent 65%);
}

.rail {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    105deg,
    transparent 40%,
    rgba(232, 220, 196, 0.02) 50%,
    transparent 60%
  );
}

.side,
.stage {
  position: relative;
  z-index: 1;
  min-height: 0;
}

.stage {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: linear-gradient(180deg, rgba(18, 21, 28, 0.5), rgba(10, 12, 15, 0.2));
}

.top {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 22px;
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(12px);
  background: rgba(10, 12, 15, 0.35);
}

.titles {
  flex: 1;
  min-width: 0;
}

.eyebrow {
  margin: 0;
  font-size: 0.68rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--ember);
}

.titles h1 {
  margin: 2px 0 0;
  font-family: var(--serif);
  font-weight: 400;
  font-size: 1.45rem;
  letter-spacing: -0.02em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.actions {
  display: flex;
  gap: 8px;
}

.ghost,
.nav-btn {
  border: 1px solid var(--line);
  background: transparent;
  border-radius: 10px;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 0.84rem;
}

.ghost:hover,
.nav-btn:hover {
  border-color: rgba(232, 160, 84, 0.4);
}

.ghost.danger {
  color: var(--rose);
  border-color: rgba(224, 122, 122, 0.3);
}

.ghost:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.nav-btn {
  display: none;
}

.hero {
  flex: 1;
  display: grid;
  place-items: center;
  padding: 32px 24px;
}

.hero-card {
  width: min(640px, 100%);
  padding: 36px 32px;
  border-radius: 24px;
  border: 1px solid var(--line-strong);
  background:
    linear-gradient(145deg, rgba(232, 160, 84, 0.08), transparent 40%),
    var(--panel);
  box-shadow: var(--shadow);
  animation: rise 0.5s ease both;
}

.hero-card h2 {
  margin: 10px 0 14px;
  font-family: var(--serif);
  font-weight: 400;
  font-size: clamp(1.8rem, 4vw, 2.55rem);
  line-height: 1.15;
  letter-spacing: -0.03em;
}

.hero-card h2 em {
  font-style: italic;
  color: var(--ember);
}

.lede {
  margin: 0 0 22px;
  color: var(--cream-dim);
  line-height: 1.65;
  font-size: 0.98rem;
}

.prompt-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.prompt {
  text-align: left;
  border: 1px solid var(--line);
  background: rgba(0, 0, 0, 0.2);
  border-radius: 999px;
  padding: 10px 14px;
  cursor: pointer;
  font-size: 0.84rem;
  color: var(--cream-dim);
  transition: 0.15s ease;
}

.prompt:hover {
  color: var(--cream);
  border-color: rgba(232, 160, 84, 0.45);
  background: var(--ember-soft);
}

.composer {
  padding: 12px 20px 18px;
}

.box {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  padding: 12px;
  border-radius: 18px;
  border: 1px solid var(--line-strong);
  background: rgba(22, 26, 34, 0.9);
  box-shadow: var(--shadow);
}

.box:focus-within {
  border-color: rgba(232, 160, 84, 0.45);
  box-shadow: 0 0 0 3px var(--ember-soft), var(--shadow);
}

.box textarea {
  flex: 1;
  resize: none;
  border: 0;
  outline: none;
  background: transparent;
  min-height: 28px;
  max-height: 160px;
  line-height: 1.5;
  padding: 8px 6px;
}

.send {
  flex-shrink: 0;
  border: 0;
  border-radius: 12px;
  padding: 12px 16px;
  font-weight: 700;
  cursor: pointer;
  color: #1a1208;
  background: linear-gradient(135deg, #c9843d, #e8a054);
}

.send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.status {
  margin-top: 10px;
  text-align: center;
  font-size: 0.75rem;
  color: var(--cream-faint);
  font-family: var(--mono);
}

.err {
  color: var(--rose);
}

.scrim {
  display: none;
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (max-width: 900px) {
  .console {
    grid-template-columns: 1fr;
  }

  .side {
    position: fixed;
    inset: 0 auto 0 0;
    width: min(300px, 88vw);
    z-index: 30;
    transform: translateX(-105%);
    transition: transform 0.25s ease;
  }

  .console.nav .side {
    transform: none;
  }

  .nav-btn {
    display: inline-flex;
  }

  .scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 25;
    background: rgba(0, 0, 0, 0.45);
  }
}
</style>
