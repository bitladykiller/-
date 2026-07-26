<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
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

const numerals = ["Ⅰ", "Ⅱ", "Ⅲ"];
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
  try {
    await store.refreshConversations();
  } catch {
    /* 后端离线时列表拉取失败不应阻断页面挂载，状态由健康灯呈现 */
  }
  healthTimer = window.setInterval(() => {
    void store.refreshHealth();
  }, 30000);
});

onUnmounted(() => {
  if (healthTimer) window.clearInterval(healthTimer);
});
</script>

<template>
  <div class="console" :class="{ nav: mobileNav }">
    <SidebarPanel class="side" />

    <section class="stage">
      <header class="top reveal">
        <button type="button" class="ghost-btn nav-btn" @click="mobileNav = !mobileNav">
          ☰ 菜单
        </button>
        <div class="titles">
          <p class="eyebrow">◆ Smart-Home Concierge</p>
          <h1>{{ activeTitle }}</h1>
        </div>
        <div class="actions">
          <button type="button" class="ghost-btn" @click="uploadOpen = true">
            <span class="glyph">❖</span> 知识文档
          </button>
          <button
            type="button"
            class="ghost-btn danger"
            :disabled="!conversationId"
            @click="onDelete"
          >
            删除会话
          </button>
        </div>
      </header>

      <div v-if="!messages.length" class="hero">
        <div class="hero-plaque plaque deco-corners reveal" style="--d: 0.08s">
          <div class="rays" aria-hidden="true" />
          <div class="monogram" aria-hidden="true"><span>AG</span></div>
          <p class="eyebrow center">AssistGen · Concierge Desk</p>
          <h2>以礼宾之道，<br aria-hidden="true" /><em>接通</em>图谱与文档智能。</h2>
          <p class="lede">
            前后端分离部署。会话、SSE 流式回答、知识入库均对接现有 FastAPI。
            经营范围内的智能家居问题可直接提问。
          </p>
          <div class="deco-rule" aria-hidden="true"><span class="deco-diamond" /></div>
          <div class="prompt-grid">
            <button
              v-for="(p, i) in prompts"
              :key="p"
              type="button"
              class="prompt"
              @click="store.send(p)"
            >
              <span class="numeral">{{ numerals[i] }}</span>
              <span class="ptext">{{ p }}</span>
              <span class="parrow" aria-hidden="true">✦</span>
            </button>
          </div>
          <div class="caps" aria-hidden="true">
            <span>图谱检索</span>
            <span>文档智能</span>
            <span>会话记忆</span>
          </div>
        </div>
      </div>
      <MessageList v-else :messages="messages" />

      <footer class="composer reveal" style="--d: 0.16s">
        <div class="box plaque deco-corners">
          <textarea
            id="composer-input"
            v-model="draft"
            rows="1"
            placeholder="写下你的问题…（Enter 发送）"
            :disabled="streaming"
            @input="resize"
            @keydown="onKey"
          />
          <button
            type="button"
            class="gold-btn send"
            :disabled="streaming || !draft.trim()"
            @click="onSend"
          >
            <span v-if="streaming" class="fan-spinner" aria-hidden="true" />
            <span v-else class="send-glyph" aria-hidden="true">✦</span>
            {{ streaming ? "生成中" : "发送" }}
          </button>
        </div>
        <div class="status">
          <span class="tick" aria-hidden="true">✧</span>
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
  z-index: 1;
  display: grid;
  grid-template-columns: 316px 1fr;
  height: 100%;
  max-width: 1520px;
  margin: 0 auto;
}

.side,
.stage {
  position: relative;
  min-height: 0;
}

.stage {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: linear-gradient(180deg, rgba(16, 23, 17, 0.5), rgba(7, 11, 9, 0.15));
}

/* ---------- 顶栏 ---------- */
.top {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 24px;
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(12px);
  background: rgba(7, 11, 9, 0.4);
}

/* 顶栏底线中央的菱形铆钉 */
.top::after {
  content: "";
  position: absolute;
  left: 50%;
  bottom: -4px;
  width: 7px;
  height: 7px;
  transform: translateX(-50%) rotate(45deg);
  background: var(--gold-deep);
  box-shadow: 0 0 8px var(--gold-glow);
}

.titles {
  flex: 1;
  min-width: 0;
}

.titles h1 {
  margin: 4px 0 0;
  font-family: var(--serif);
  font-weight: 500;
  font-size: 1.5rem;
  letter-spacing: 0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.actions {
  display: flex;
  gap: 8px;
}

.glyph {
  color: var(--gold);
  font-size: 0.9em;
}

.nav-btn {
  display: none;
}

/* ---------- 鎏金大厅（空状态） ---------- */
.hero {
  flex: 1;
  display: grid;
  place-items: center;
  padding: 32px 24px;
  overflow: auto;
}

.hero-plaque {
  position: relative;
  width: min(680px, 100%);
  padding: 46px 40px 34px;
  text-align: center;
  overflow: hidden;
}

/* 冠顶扇形放射纹 */
.rays {
  position: absolute;
  left: 50%;
  top: 0;
  width: 620px;
  height: 240px;
  transform: translateX(-50%);
  pointer-events: none;
  background: repeating-conic-gradient(
    from -90deg at 50% 0%,
    rgba(216, 178, 106, 0.35) 0deg 0.9deg,
    transparent 0.9deg 6deg
  );
  -webkit-mask-image: radial-gradient(115% 95% at 50% 0%, #000 12%, transparent 68%);
  mask-image: radial-gradient(115% 95% at 50% 0%, #000 12%, transparent 68%);
  animation: ray-breathe 7s ease-in-out infinite;
}

.monogram {
  position: relative;
  width: 56px;
  height: 56px;
  margin: 4px auto 22px;
  display: grid;
  place-items: center;
  transform: rotate(45deg);
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(216, 178, 106, 0.18), rgba(216, 178, 106, 0.04));
  box-shadow: 0 0 30px rgba(216, 178, 106, 0.22), inset 0 0 16px rgba(216, 178, 106, 0.12);
}

.monogram span {
  transform: rotate(-45deg);
  font-family: var(--serif);
  font-size: 1.02rem;
  letter-spacing: 0.04em;
  color: var(--gold-bright);
}

.eyebrow.center {
  text-align: center;
}

.hero-plaque h2 {
  margin: 14px 0 16px;
  font-family: var(--serif);
  font-weight: 600;
  font-size: clamp(1.8rem, 4vw, 2.6rem);
  line-height: 1.22;
  letter-spacing: 0.01em;
  text-wrap: balance;
}

.hero-plaque h2 em {
  font-style: normal;
  color: var(--gold);
  background: linear-gradient(180deg, transparent 68%, var(--gold-soft) 68%);
  padding: 0 2px;
}

.lede {
  margin: 0 auto 24px;
  max-width: 44em;
  color: var(--ivory-dim);
  line-height: 1.75;
  font-size: 0.95rem;
}

.hero-plaque .deco-rule {
  margin: 0 auto 24px;
  width: min(360px, 80%);
}

.prompt-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.prompt {
  position: relative;
  display: grid;
  gap: 10px;
  justify-items: start;
  text-align: left;
  padding: 14px 14px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: rgba(7, 11, 9, 0.45);
  color: var(--ivory-dim);
  cursor: pointer;
  transition: transform 0.18s var(--ease), border-color 0.18s ease, background 0.18s ease,
    color 0.18s ease, box-shadow 0.18s ease;
}

.prompt:hover {
  transform: translateY(-2px);
  color: var(--ivory);
  border-color: var(--line-strong);
  background: linear-gradient(160deg, var(--gold-soft), rgba(7, 11, 9, 0.4));
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.35);
}

.numeral {
  font-family: var(--serif);
  font-size: 0.92rem;
  color: var(--gold);
  border-bottom: 1px solid var(--line-strong);
  padding-bottom: 3px;
}

.ptext {
  font-size: 0.86rem;
  line-height: 1.5;
}

.parrow {
  position: absolute;
  right: 12px;
  top: 12px;
  font-size: 0.7rem;
  color: var(--gold);
  opacity: 0;
  transition: opacity 0.18s ease, transform 0.18s var(--ease);
  transform: translateX(-4px);
}

.prompt:hover .parrow {
  opacity: 1;
  transform: none;
}

.caps {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  justify-content: center;
  margin-top: 22px;
  color: var(--ivory-faint);
  font-size: 0.76rem;
  letter-spacing: 0.14em;
}

.caps span::before {
  content: "◇ ";
  color: var(--gold);
}

/* ---------- 侍应台（输入区） ---------- */
.composer {
  padding: 14px 22px 24px;
}

.box {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  padding: 14px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.box:focus-within {
  border-color: rgba(216, 178, 106, 0.5);
  box-shadow: 0 0 0 3px var(--gold-soft), 0 0 34px rgba(216, 178, 106, 0.12),
    var(--shadow-soft);
}

.box textarea {
  flex: 1;
  resize: none;
  border: 0;
  outline: none;
  background: transparent;
  min-height: 28px;
  max-height: 160px;
  line-height: 1.55;
  padding: 8px 6px;
}

.box textarea::placeholder {
  color: var(--ivory-faint);
}

.send {
  flex-shrink: 0;
  padding: 12px 18px;
}

.send-glyph {
  font-size: 0.82em;
}

.status {
  margin-top: 10px;
  display: flex;
  justify-content: center;
  gap: 6px;
  font-size: 0.72rem;
  color: var(--ivory-faint);
  font-family: var(--mono);
  letter-spacing: 0.06em;
}

.tick {
  color: var(--gold);
}

.err {
  color: var(--garnet);
}

.scrim {
  display: none;
}

/* ---------- 移动端 ---------- */
@media (max-width: 900px) {
  .console {
    grid-template-columns: 1fr;
  }

  .side {
    position: fixed;
    inset: 0 auto 0 0;
    width: min(316px, 88vw);
    z-index: 30;
    transform: translateX(-105%);
    transition: transform 0.25s var(--ease);
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
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(3px);
  }

  .prompt-grid {
    grid-template-columns: 1fr;
  }

  .hero-plaque {
    padding: 36px 22px 26px;
  }
}

@media (max-width: 640px) {
  .top {
    padding: 12px 14px;
    gap: 10px;
  }

  /* 窄屏收起英文眉题，把空间留给标题与操作 */
  .titles .eyebrow {
    display: none;
  }

  .titles h1 {
    margin: 0;
    font-size: 1.16rem;
  }

  .actions .ghost-btn {
    padding: 8px 10px;
    font-size: 0.76rem;
  }

  .composer {
    padding: 12px 14px 18px;
  }
}
</style>
