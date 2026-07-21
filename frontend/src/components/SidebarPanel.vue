<script setup lang="ts">
import { storeToRefs } from "pinia";
import { useChatStore } from "@/stores/chat";
import { formatTime } from "@/utils/format";

const store = useChatStore();
const { conversations, conversationId, userId, healthy } = storeToRefs(store);

function onUserChange(e: Event) {
  const v = Number((e.target as HTMLInputElement).value);
  store.setUserId(Number.isFinite(v) && v >= 1 ? Math.floor(v) : 1);
  void store.refreshConversations();
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand-block">
      <div class="mark" aria-hidden="true">AG</div>
      <div>
        <div class="brand-name">AssistGen</div>
        <div class="brand-tag">Concierge Console</div>
      </div>
    </div>

    <button type="button" class="btn primary block" @click="store.newChat()">
      <span class="plus">＋</span> 开启新会话
    </button>

    <div class="panel">
      <label class="label">
        用户 ID
        <input
          type="number"
          min="1"
          :value="userId"
          @change="onUserChange"
        />
      </label>
      <div class="health">
        <span
          class="pulse"
          :class="{
            ok: healthy === true,
            bad: healthy === false,
          }"
        />
        <span v-if="healthy === true">Backend online</span>
        <span v-else-if="healthy === false">Backend offline</span>
        <span v-else>Checking…</span>
      </div>
    </div>

    <div class="list-head">
      <span>会话档案</span>
      <button type="button" class="linkish" @click="store.refreshConversations()">
        刷新
      </button>
    </div>

    <ul class="conv-list">
      <li v-for="c in conversations" :key="c.id">
        <button
          type="button"
          class="conv"
          :class="{ active: c.id === conversationId }"
          @click="store.selectConversation(c)"
        >
          <span class="title">{{ c.title }}</span>
          <span class="meta">#{{ c.id }} · {{ formatTime(c.created_at) }}</span>
        </button>
      </li>
    </ul>
    <p v-if="!conversations.length" class="empty">尚无归档会话</p>

    <div class="foot">
      <a href="/docs" target="_blank" rel="noopener">OpenAPI</a>
      <span class="sep">·</span>
      <span>分离部署</span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  padding: 22px 16px 16px;
  background:
    linear-gradient(180deg, rgba(232, 160, 84, 0.06), transparent 28%),
    var(--panel);
  border-right: 1px solid var(--line);
}

.brand-block {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 4px 2px 8px;
}

.mark {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-family: var(--mono);
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ember);
  background: var(--ember-soft);
  border: 1px solid rgba(232, 160, 84, 0.35);
}

.brand-name {
  font-family: var(--serif);
  font-size: 1.35rem;
  letter-spacing: -0.02em;
  line-height: 1.1;
}

.brand-tag {
  font-size: 0.72rem;
  color: var(--cream-faint);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-top: 3px;
}

.btn {
  border: 1px solid var(--line-strong);
  background: transparent;
  border-radius: 12px;
  padding: 11px 14px;
  cursor: pointer;
  transition: 0.15s ease;
}

.btn:hover {
  border-color: rgba(232, 160, 84, 0.45);
  background: rgba(232, 160, 84, 0.08);
}

.btn.primary {
  background: linear-gradient(135deg, #c9843d, #e8a054 55%, #f0b875);
  color: #1a1208;
  border: none;
  font-weight: 700;
  box-shadow: 0 10px 30px rgba(232, 160, 84, 0.22);
}

.btn.primary:hover {
  filter: brightness(1.05);
  background: linear-gradient(135deg, #c9843d, #e8a054 55%, #f0b875);
}

.btn.block {
  width: 100%;
}

.plus {
  margin-right: 4px;
}

.panel {
  display: grid;
  gap: 10px;
  padding: 12px;
  border-radius: 14px;
  border: 1px solid var(--line);
  background: rgba(0, 0, 0, 0.2);
}

.label {
  display: grid;
  gap: 6px;
  font-size: 0.72rem;
  color: var(--cream-faint);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.label input {
  padding: 9px 10px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: var(--ink-2);
  outline: none;
}

.label input:focus {
  border-color: rgba(232, 160, 84, 0.5);
  box-shadow: 0 0 0 3px var(--ember-soft);
}

.health {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  color: var(--cream-dim);
}

.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--cream-faint);
}

.pulse.ok {
  background: var(--mint);
  box-shadow: 0 0 12px rgba(125, 206, 160, 0.55);
}

.pulse.bad {
  background: var(--rose);
  box-shadow: 0 0 12px rgba(224, 122, 122, 0.45);
}

.list-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.72rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--cream-faint);
  padding: 4px 2px 0;
}

.linkish {
  border: 0;
  background: none;
  color: var(--ember);
  cursor: pointer;
  font-size: 0.75rem;
  letter-spacing: 0.04em;
  text-transform: none;
}

.conv-list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow: auto;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
}

.conv {
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 12px;
  padding: 11px 12px;
  cursor: pointer;
  display: grid;
  gap: 4px;
  transition: 0.15s ease;
}

.conv:hover {
  background: rgba(255, 255, 255, 0.03);
  border-color: var(--line);
}

.conv.active {
  background: var(--ember-soft);
  border-color: rgba(232, 160, 84, 0.35);
}

.title {
  font-size: 0.92rem;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.meta {
  font-family: var(--mono);
  font-size: 0.68rem;
  color: var(--cream-faint);
}

.empty {
  text-align: center;
  color: var(--cream-faint);
  font-size: 0.82rem;
  margin: 20px 0;
}

.foot {
  font-size: 0.75rem;
  color: var(--cream-faint);
  padding-top: 10px;
  border-top: 1px solid var(--line);
}

.foot a {
  text-decoration: none;
}

.sep {
  margin: 0 6px;
  opacity: 0.5;
}
</style>
