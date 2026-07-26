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
    <div class="crest reveal">
      <div class="crest-rays" aria-hidden="true" />
      <div class="mark" aria-hidden="true"><span>AG</span></div>
      <div class="brand-name">AssistGen</div>
      <div class="brand-tag">智能客服 · 礼宾控制台</div>
    </div>

    <button type="button" class="gold-btn block reveal" style="--d: 0.05s" @click="store.newChat()">
      <span aria-hidden="true">✦</span> 开启新会话
    </button>

    <div class="panel plaque reveal" style="--d: 0.1s">
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
          class="gem"
          :class="{
            ok: healthy === true,
            bad: healthy === false,
          }"
        />
        <span v-if="healthy === true">后端在线</span>
        <span v-else-if="healthy === false">后端离线</span>
        <span v-else>检查中…</span>
      </div>
    </div>

    <div class="list-head reveal" style="--d: 0.14s">
      <span><i class="tick" aria-hidden="true">◆</i> 会话档案</span>
      <button type="button" class="linkish" @click="store.refreshConversations()">
        刷新
      </button>
    </div>

    <ul v-if="conversations.length" class="conv-list">
      <li v-for="c in conversations" :key="c.id">
        <button
          type="button"
          class="conv"
          :class="{ active: c.id === conversationId }"
          @click="store.selectConversation(c)"
        >
          <span class="title">{{ c.title }}</span>
          <span class="meta">№{{ c.id }} · {{ formatTime(c.created_at) }}</span>
        </button>
      </li>
    </ul>
    <p v-else class="empty">
      <span aria-hidden="true">✧</span> 尚无归档会话
    </p>

    <div class="foot">
      <a href="/docs" target="_blank" rel="noopener">OpenAPI</a>
      <span class="sep" aria-hidden="true">◆</span>
      <span>前后端分离</span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  padding: 24px 16px 16px;
  background:
    linear-gradient(180deg, rgba(216, 178, 106, 0.06), transparent 30%),
    linear-gradient(180deg, var(--lacquer-2), var(--lacquer));
  border-right: 1px solid var(--line);
}

/* ---------- 纹章冠顶 ---------- */
.crest {
  position: relative;
  display: grid;
  justify-items: center;
  gap: 2px;
  padding: 18px 0 10px;
  overflow: hidden;
}

.crest-rays {
  position: absolute;
  left: 50%;
  top: 0;
  width: 300px;
  height: 120px;
  transform: translateX(-50%);
  pointer-events: none;
  background: repeating-conic-gradient(
    from -90deg at 50% 0%,
    rgba(216, 178, 106, 0.3) 0deg 1deg,
    transparent 1deg 7deg
  );
  -webkit-mask-image: radial-gradient(120% 100% at 50% 0%, #000 8%, transparent 62%);
  mask-image: radial-gradient(120% 100% at 50% 0%, #000 8%, transparent 62%);
  animation: ray-breathe 8s ease-in-out infinite;
}

.mark {
  position: relative;
  width: 46px;
  height: 46px;
  margin-bottom: 14px;
  display: grid;
  place-items: center;
  transform: rotate(45deg);
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(216, 178, 106, 0.18), rgba(216, 178, 106, 0.05));
  box-shadow: 0 0 24px rgba(216, 178, 106, 0.2), inset 0 0 12px rgba(216, 178, 106, 0.12);
}

.mark span {
  transform: rotate(-45deg);
  font-family: var(--serif);
  font-size: 0.86rem;
  letter-spacing: 0.04em;
  color: var(--gold-bright);
}

.brand-name {
  position: relative;
  font-family: var(--serif);
  font-size: 1.52rem;
  letter-spacing: 0.06em;
  line-height: 1.1;
}

.brand-tag {
  position: relative;
  font-family: var(--mono);
  font-size: 0.62rem;
  color: var(--ivory-faint);
  letter-spacing: 0.3em;
  margin-top: 6px;
}

.block {
  width: 100%;
}

/* ---------- 用户与健康 ---------- */
.panel {
  display: grid;
  gap: 12px;
  padding: 14px;
}

.label {
  display: grid;
  gap: 7px;
  font-family: var(--mono);
  font-size: 0.64rem;
  color: var(--ivory-faint);
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.label input {
  padding: 9px 11px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: rgba(7, 11, 9, 0.6);
  outline: none;
  font-family: var(--sans);
  letter-spacing: normal;
  transition: border-color 0.16s ease, box-shadow 0.16s ease;
}

.label input:focus {
  border-color: rgba(216, 178, 106, 0.5);
  box-shadow: 0 0 0 3px var(--gold-soft);
}

.health {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.82rem;
  color: var(--ivory-dim);
}

/* ---------- 会话档案 ---------- */
.list-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--mono);
  font-size: 0.66rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--ivory-faint);
  padding: 4px 2px 0;
}

.list-head .tick {
  font-style: normal;
  color: var(--gold);
  font-size: 0.7em;
  margin-right: 4px;
}

.linkish {
  border: 0;
  background: none;
  color: var(--gold);
  cursor: pointer;
  font-size: 0.72rem;
  letter-spacing: 0.08em;
}

.linkish:hover {
  color: var(--gold-bright);
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
  position: relative;
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 12px;
  padding: 11px 12px 11px 16px;
  cursor: pointer;
  display: grid;
  gap: 5px;
  transition: background 0.16s ease, border-color 0.16s ease;
}

/* 左侧鎏金立柱：激活时点亮 */
.conv::before {
  content: "";
  position: absolute;
  left: 5px;
  top: 22%;
  bottom: 22%;
  width: 2px;
  border-radius: 2px;
  background: transparent;
  transition: background 0.16s ease, box-shadow 0.16s ease;
}

.conv:hover {
  background: rgba(216, 178, 106, 0.05);
  border-color: var(--line);
}

.conv.active {
  background: var(--gold-soft);
  border-color: var(--line-strong);
}

.conv.active::before {
  background: var(--gold);
  box-shadow: 0 0 10px var(--gold-glow);
}

.title {
  font-size: 0.9rem;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv.active .title {
  color: var(--gold-bright);
}

.meta {
  font-family: var(--mono);
  font-size: 0.64rem;
  color: var(--ivory-faint);
  letter-spacing: 0.03em;
}

.empty {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  color: var(--ivory-faint);
  font-size: 0.82rem;
  margin: 0;
}

.empty span {
  color: var(--gold);
}

/* ---------- 座底 ---------- */
.foot {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
  font-family: var(--mono);
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  color: var(--ivory-faint);
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.foot a {
  text-decoration: none;
}

.foot a:hover {
  color: var(--gold-bright);
}

.sep {
  font-size: 0.5rem;
  color: var(--gold-deep);
}
</style>
