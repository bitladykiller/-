<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import type { ChatMessage } from "@/api/types";
import { formatMessageHtml } from "@/utils/format";

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
      <div class="avatar" aria-hidden="true">
        {{ m.role === "user" ? "You" : m.role === "system" ? "Note" : "AG" }}
      </div>
      <div class="body">
        <div
          class="bubble"
          :class="{ streaming: m.streaming, system: m.role === 'system' }"
          v-html="formatMessageHtml(m.content || (m.streaming ? '…' : ''))"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.messages {
  flex: 1;
  overflow: auto;
  padding: 28px 28px 16px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.row {
  display: flex;
  gap: 12px;
  max-width: min(760px, 100%);
  animation: in 0.35s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.row.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.row.assistant,
.row.system {
  align-self: flex-start;
}

.avatar {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-family: var(--mono);
  font-size: 0.62rem;
  letter-spacing: 0.04em;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.03);
  color: var(--cream-dim);
}

.row.user .avatar {
  color: #1a1208;
  background: linear-gradient(145deg, #e8a054, #c9843d);
  border-color: transparent;
}

.row.assistant .avatar {
  color: var(--mint);
  border-color: rgba(125, 206, 160, 0.3);
}

.bubble {
  padding: 12px 15px;
  border-radius: 16px;
  line-height: 1.6;
  font-size: 0.95rem;
  word-break: break-word;
}

.row.user .bubble {
  background: linear-gradient(145deg, #2a241c, #1e1a14);
  border: 1px solid rgba(232, 160, 84, 0.28);
  border-bottom-right-radius: 6px;
  color: var(--cream);
}

.row.assistant .bubble {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--line);
  border-bottom-left-radius: 6px;
}

.bubble.system {
  font-size: 0.85rem;
  color: var(--cream-dim);
  border-style: dashed;
  opacity: 0.9;
}

.bubble.streaming::after {
  content: "";
  display: inline-block;
  width: 7px;
  height: 1.05em;
  margin-left: 4px;
  vertical-align: text-bottom;
  background: var(--ember);
  border-radius: 1px;
  animation: blink 0.9s steps(1) infinite;
}

.bubble :deep(code) {
  font-family: var(--mono);
  font-size: 0.86em;
  padding: 0.1em 0.35em;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.35);
}

.bubble :deep(strong) {
  color: #fff;
  font-weight: 650;
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

@keyframes in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
</style>
