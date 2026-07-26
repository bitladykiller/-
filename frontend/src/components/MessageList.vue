<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import type { ChatMessage } from "@/api/types";
import { formatClock, formatMessageHtml } from "@/utils/format";

const props = defineProps<{
  messages: ChatMessage[];
}>();

const root = ref<HTMLElement | null>(null);

watch(
  () =>
    props.messages
      .map((m) => `${m.id}:${m.content.length}:${m.streaming ? 1 : 0}`)
      .join("|"),
  async () => {
    await nextTick();
    if (root.value) root.value.scrollTop = root.value.scrollHeight;
  },
);
</script>

<template>
  <div ref="root" class="messages">
    <div v-for="m in messages" :key="m.id" class="row" :class="m.role">
      <div class="medal" aria-hidden="true">
        <span>{{ m.role === "user" ? "YOU" : m.role === "system" ? "✧" : "AG" }}</span>
      </div>
      <div class="body">
        <div
          class="bubble"
          :class="{ streaming: m.streaming, system: m.role === 'system' }"
          v-html="formatMessageHtml(m.content || (m.streaming ? '…' : ''))"
        />
        <div v-if="m.role !== 'system'" class="stamp">
          {{ formatClock(m.createdAt) }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.messages {
  flex: 1;
  overflow: auto;
  padding: 30px 30px 16px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.row {
  display: flex;
  gap: 14px;
  max-width: min(780px, 100%);
  animation: rise-in 0.4s var(--ease) both;
}

.row.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.row.assistant,
.row.system {
  align-self: flex-start;
}

/* ---------- 菱形纹章头像 ---------- */
.medal {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  margin: 4px 6px 0;
  display: grid;
  place-items: center;
  transform: rotate(45deg);
  border-radius: 9px;
  border: 1px solid var(--line);
  background: rgba(216, 178, 106, 0.04);
}

.medal span {
  transform: rotate(-45deg);
  font-family: var(--mono);
  font-size: 0.5rem;
  letter-spacing: 0.08em;
  color: var(--ivory-faint);
}

.row.user .medal {
  border-color: transparent;
  background: linear-gradient(135deg, var(--gold-deep), var(--gold) 55%, var(--gold-bright));
  box-shadow: 0 0 18px rgba(216, 178, 106, 0.3);
}

.row.user .medal span {
  color: #241705;
  font-weight: 700;
}

.row.assistant .medal {
  border-color: rgba(142, 201, 168, 0.4);
  background: rgba(142, 201, 168, 0.06);
  box-shadow: 0 0 14px rgba(142, 201, 168, 0.14);
}

.row.assistant .medal span {
  color: var(--jade);
}

/* ---------- 气泡 ---------- */
.body {
  min-width: 0;
}

.bubble {
  position: relative;
  padding: 13px 16px;
  border-radius: 16px;
  line-height: 1.68;
  font-size: 0.94rem;
  word-break: break-word;
}

/* 角落宝石钉 */
.bubble::before {
  content: "";
  position: absolute;
  width: 6px;
  height: 6px;
  transform: rotate(45deg);
}

.row.user .bubble {
  background: linear-gradient(150deg, #262012, #1a160c);
  border: 1px solid rgba(216, 178, 106, 0.32);
  border-top-right-radius: 5px;
  color: var(--ivory);
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.3);
}

.row.user .bubble::before {
  top: -3.5px;
  right: 16px;
  background: var(--gold);
  box-shadow: 0 0 8px var(--gold-glow);
}

.row.assistant .bubble {
  background: linear-gradient(180deg, var(--lacquer-3), var(--lacquer));
  border: 1px solid var(--line);
  border-bottom-left-radius: 5px;
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.04);
}

.row.assistant .bubble::before {
  bottom: -3.5px;
  left: 16px;
  background: var(--jade);
  box-shadow: 0 0 8px rgba(142, 201, 168, 0.5);
}

.bubble.system {
  font-size: 0.84rem;
  color: var(--ivory-dim);
  background: transparent;
  border: 1px dashed var(--line-strong);
  box-shadow: none;
}

.bubble.system::before {
  display: none;
}

/* 流式生成的鎏金光标 */
.bubble.streaming::after {
  content: "";
  display: inline-block;
  width: 7px;
  height: 1.05em;
  margin-left: 5px;
  vertical-align: text-bottom;
  background: linear-gradient(180deg, var(--gold-bright), var(--gold-deep));
  border-radius: 1px;
  box-shadow: 0 0 8px var(--gold-glow);
  animation: caret-blink 0.9s steps(1) infinite;
}

/* 时刻铭牌 */
.stamp {
  margin-top: 6px;
  font-family: var(--mono);
  font-size: 0.6rem;
  letter-spacing: 0.1em;
  color: var(--ivory-faint);
}

.row.user .stamp {
  text-align: right;
}

/* 富文本细节 */
.bubble :deep(code) {
  font-family: var(--mono);
  font-size: 0.84em;
  padding: 0.12em 0.4em;
  border-radius: 5px;
  color: var(--gold-bright);
  background: rgba(7, 11, 9, 0.55);
  border: 1px solid var(--line-faint);
}

.bubble :deep(strong) {
  color: #fff;
  font-weight: 650;
}
</style>
