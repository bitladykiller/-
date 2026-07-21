<script setup lang="ts">
import { ref } from "vue";
import { useChatStore } from "@/stores/chat";

const open = defineModel<boolean>("open", { required: true });
const store = useChatStore();

const file = ref<File | null>(null);
const busy = ref(false);
const label = ref("");
const pct = ref(0);
const err = ref<string | null>(null);

function onPick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (f) file.value = f;
}

function onDrop(e: DragEvent) {
  e.preventDefault();
  const f = e.dataTransfer?.files?.[0];
  if (f) file.value = f;
}

async function submit() {
  if (!file.value || busy.value) return;
  busy.value = true;
  err.value = null;
  try {
    await store.upload(file.value, (l, p) => {
      label.value = l;
      pct.value = p;
    });
    open.value = false;
    file.value = null;
    label.value = "";
    pct.value = 0;
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div v-if="open" class="mask" @click.self="open = false">
    <div class="sheet" role="dialog" aria-label="上传文档">
      <header>
        <div>
          <p class="kicker">Knowledge ingest</p>
          <h2>上传知识文档</h2>
        </div>
        <button type="button" class="x" @click="open = false">×</button>
      </header>
      <p class="desc">
        支持 <code>.md</code> / <code>.pdf</code> / <code>.docx</code>。文件落入后端后异步解析进
        Milvus，供 RAG 检索。
      </p>

      <label
        class="zone"
        @dragover.prevent
        @drop="onDrop"
      >
        <input type="file" hidden accept=".md,.markdown,.pdf,.docx" @change="onPick" />
        <span class="zone-title">拖入文件或点击选择</span>
        <span v-if="file" class="zone-file">{{ file.name }}</span>
      </label>

      <div v-if="busy || label" class="prog">
        <div class="bar"><i :style="{ width: pct + '%' }" /></div>
        <span>{{ label }}</span>
      </div>
      <p v-if="err" class="err">{{ err }}</p>

      <footer>
        <button type="button" class="ghost" @click="open = false">取消</button>
        <button
          type="button"
          class="go"
          :disabled="!file || busy"
          @click="submit"
        >
          {{ busy ? "处理中…" : "开始入库" }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(8px);
  display: grid;
  place-items: center;
  padding: 20px;
}

.sheet {
  width: min(460px, 100%);
  background: var(--panel-2);
  border: 1px solid var(--line-strong);
  border-radius: 20px;
  padding: 22px;
  box-shadow: var(--shadow);
  animation: up 0.28s ease both;
}

header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.kicker {
  margin: 0 0 4px;
  font-size: 0.7rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ember);
}

h2 {
  margin: 0;
  font-family: var(--serif);
  font-size: 1.55rem;
  font-weight: 400;
  letter-spacing: -0.02em;
}

.x {
  border: 0;
  background: transparent;
  font-size: 1.4rem;
  cursor: pointer;
  color: var(--cream-dim);
  line-height: 1;
}

.desc {
  margin: 12px 0 16px;
  color: var(--cream-dim);
  font-size: 0.9rem;
  line-height: 1.55;
}

.desc code {
  font-family: var(--mono);
  font-size: 0.82em;
  color: var(--cream);
}

.zone {
  display: grid;
  place-items: center;
  gap: 8px;
  min-height: 140px;
  border-radius: 14px;
  border: 1.5px dashed var(--line-strong);
  background: rgba(0, 0, 0, 0.2);
  cursor: pointer;
  transition: 0.15s ease;
}

.zone:hover {
  border-color: rgba(232, 160, 84, 0.5);
  background: var(--ember-soft);
}

.zone-title {
  color: var(--cream-dim);
}

.zone-file {
  font-family: var(--mono);
  font-size: 0.8rem;
  color: var(--ember);
  word-break: break-all;
  padding: 0 12px;
  text-align: center;
}

.prog {
  margin-top: 14px;
  display: grid;
  gap: 8px;
  font-size: 0.82rem;
  color: var(--cream-dim);
}

.bar {
  height: 6px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.06);
  overflow: hidden;
}

.bar i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #c9843d, #e8a054, #7dcea0);
  transition: width 0.25s ease;
}

.err {
  color: var(--rose);
  font-size: 0.85rem;
  margin: 10px 0 0;
}

footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

.ghost,
.go {
  border-radius: 11px;
  padding: 10px 14px;
  cursor: pointer;
  border: 1px solid var(--line);
  background: transparent;
}

.go {
  background: linear-gradient(135deg, #c9843d, #e8a054);
  color: #1a1208;
  border: none;
  font-weight: 700;
}

.go:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

@keyframes up {
  from {
    opacity: 0;
    transform: translateY(12px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
</style>
