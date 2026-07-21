<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import {
  listDocuments,
  type UserDocumentSummary,
  uploadDocument,
  getUploadStatus,
} from "@/api/client";
import { useChatStore } from "@/stores/chat";

const open = defineModel<boolean>("open", { required: true });
const store = useChatStore();

type Tab = "upload" | "manage";

const tab = ref<Tab>("upload");
const file = ref<File | null>(null);
const busy = ref(false);
const label = ref("");
const pct = ref(0);
const err = ref<string | null>(null);
const docs = ref<UserDocumentSummary[]>([]);
const loadingDocs = ref(false);
const replaceDocId = ref<string | null>(null);
const replaceFile = ref<File | null>(null);

async function refreshDocs() {
  loadingDocs.value = true;
  err.value = null;
  try {
    docs.value = await listDocuments(store.userId);
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    loadingDocs.value = false;
  }
}

watch(open, (v) => {
  if (v) {
    void refreshDocs();
    tab.value = "upload";
    file.value = null;
    replaceDocId.value = null;
    replaceFile.value = null;
    err.value = null;
    label.value = "";
    pct.value = 0;
  }
});

onMounted(() => {
  if (open.value) void refreshDocs();
});

function onPick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (f) file.value = f;
}

function onDrop(e: DragEvent) {
  e.preventDefault();
  const f = e.dataTransfer?.files?.[0];
  if (f) file.value = f;
}

function onReplacePick(e: Event, docId: string) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (!f) return;
  replaceDocId.value = docId;
  replaceFile.value = f;
}

async function pollTask(taskId: string) {
  for (let i = 0; i < 120; i++) {
    const st = await getUploadStatus(taskId);
    if (st.status === "pending") {
      label.value = "排队中…";
      pct.value = 30;
    }
    if (st.status === "running") {
      label.value = "解析索引中…";
      pct.value = 60;
    }
    if (st.status === "completed") {
      const n = st.result?.chunks;
      const doc = st.result?.doc_id;
      label.value =
        typeof n === "number"
          ? `完成，${n} 个片段${doc ? ` · ${doc}` : ""}`
          : "索引完成";
      pct.value = 100;
      return st;
    }
    if (st.status === "failed") {
      throw new Error(st.error || "索引失败");
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("轮询超时");
}

async function submitCreate() {
  if (!file.value || busy.value) return;
  busy.value = true;
  err.value = null;
  try {
    label.value = "上传中…";
    pct.value = 10;
    const accepted = await uploadDocument(store.userId, file.value, {
      mode: "create",
    });
    label.value = `解析中 task=${accepted.task_id}`;
    pct.value = 25;
    await pollTask(accepted.task_id);
    await refreshDocs();
    file.value = null;
    tab.value = "manage";
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function submitReplace(docId: string) {
  if (!replaceFile.value || busy.value || replaceDocId.value !== docId) return;
  busy.value = true;
  err.value = null;
  try {
    label.value = "更新上传中…";
    pct.value = 10;
    const accepted = await uploadDocument(store.userId, replaceFile.value, {
      mode: "replace",
      docId,
    });
    if (accepted.unchanged || accepted.skipped || !accepted.task_id) {
      label.value = accepted.message || "内容未变化，已跳过 reindex";
      pct.value = 100;
    } else {
      label.value = `替换索引中 task=${accepted.task_id}`;
      pct.value = 25;
      await pollTask(accepted.task_id);
    }
    replaceDocId.value = null;
    replaceFile.value = null;
    await refreshDocs();
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    pending: "待处理",
    indexing: "索引中",
    ready: "就绪",
    failed: "失败",
  };
  return map[s] || s;
}
</script>

<template>
  <div v-if="open" class="mask" @click.self="open = false">
    <div class="sheet" role="dialog" aria-label="知识文档">
      <header>
        <div>
          <p class="kicker">Knowledge ingest</p>
          <h2>知识文档</h2>
        </div>
        <button type="button" class="x" @click="open = false">×</button>
      </header>

      <div class="tabs">
        <button
          type="button"
          :class="{ on: tab === 'upload' }"
          @click="tab = 'upload'"
        >
          上传新文档
        </button>
        <button
          type="button"
          :class="{ on: tab === 'manage' }"
          @click="
            tab = 'manage';
            refreshDocs();
          "
        >
          我的文档 / 更新
        </button>
      </div>

      <template v-if="tab === 'upload'">
        <p class="desc">
          支持 <code>.md</code> / <code>.pdf</code> / <code>.docx</code>。
          入库后分配稳定 <code>doc_id</code>（写入 MySQL + Milvus），更新时必须用同一 ID。
        </p>

        <label class="zone" @dragover.prevent @drop="onDrop">
          <input
            type="file"
            hidden
            accept=".md,.markdown,.pdf,.docx"
            @change="onPick"
          />
          <span class="zone-title">拖入文件或点击选择</span>
          <span v-if="file" class="zone-file">{{ file.name }}</span>
        </label>

        <footer>
          <button type="button" class="ghost" @click="open = false">取消</button>
          <button
            type="button"
            class="go"
            :disabled="!file || busy"
            @click="submitCreate"
          >
            {{ busy ? "处理中…" : "开始入库" }}
          </button>
        </footer>
      </template>

      <template v-else>
        <p class="desc">
          点击「选择新版本」后确认更新：后端按该行的
          <code>doc_id</code> 做 replace（软删旧向量 + 写新 version）。
        </p>
        <div class="toolbar">
          <button type="button" class="ghost sm" :disabled="loadingDocs" @click="refreshDocs">
            {{ loadingDocs ? "刷新中…" : "刷新列表" }}
          </button>
        </div>

        <div v-if="!docs.length && !loadingDocs" class="empty">暂无文档，请先上传。</div>
        <ul v-else class="doc-list">
          <li v-for="d in docs" :key="d.doc_id" class="doc">
            <div class="doc-main">
              <div class="doc-title">{{ d.title || d.original_name }}</div>
              <div class="doc-meta">
                <span class="mono">{{ d.doc_id }}</span>
                <span>· v{{ d.version }}</span>
                <span>· {{ d.chunk_count }} chunks</span>
                <span class="st" :data-s="d.status">{{ statusLabel(d.status) }}</span>
              </div>
              <div v-if="d.error_message" class="doc-err">{{ d.error_message }}</div>
            </div>
            <div class="doc-actions">
              <label class="pick">
                选择新版本
                <input
                  type="file"
                  hidden
                  accept=".md,.markdown,.pdf,.docx"
                  :disabled="busy"
                  @change="onReplacePick($event, d.doc_id)"
                />
              </label>
              <button
                type="button"
                class="go sm"
                :disabled="busy || replaceDocId !== d.doc_id || !replaceFile"
                @click="submitReplace(d.doc_id)"
              >
                {{
                  replaceDocId === d.doc_id && replaceFile
                    ? busy
                      ? "更新中…"
                      : `确认更新（${replaceFile.name}）`
                    : "更新文档"
                }}
              </button>
            </div>
          </li>
        </ul>
      </template>

      <div v-if="busy || label" class="prog">
        <div class="bar"><i :style="{ width: pct + '%' }" /></div>
        <span>{{ label }}</span>
      </div>
      <p v-if="err" class="err">{{ err }}</p>
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
  width: min(560px, 100%);
  max-height: min(86vh, 720px);
  overflow: auto;
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

.tabs {
  display: flex;
  gap: 8px;
  margin: 16px 0 12px;
}

.tabs button {
  flex: 1;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--cream-dim);
  padding: 9px 10px;
  cursor: pointer;
  font-size: 0.88rem;
}

.tabs button.on {
  background: var(--ember-soft);
  border-color: rgba(232, 160, 84, 0.45);
  color: var(--cream);
  font-weight: 600;
}

.desc {
  margin: 0 0 14px;
  color: var(--cream-dim);
  font-size: 0.88rem;
  line-height: 1.55;
}

.desc code,
.mono {
  font-family: var(--mono);
  font-size: 0.8em;
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

.toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 10px;
}

.empty {
  padding: 28px 12px;
  text-align: center;
  color: var(--cream-dim);
  font-size: 0.9rem;
}

.doc-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}

.doc {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px;
  background: rgba(0, 0, 0, 0.18);
  display: grid;
  gap: 10px;
}

.doc-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.doc-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  font-size: 0.78rem;
  color: var(--cream-dim);
}

.st {
  padding: 1px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
}

.st[data-s="ready"] {
  color: #7dcea0;
}

.st[data-s="failed"] {
  color: var(--rose);
}

.st[data-s="indexing"],
.st[data-s="pending"] {
  color: var(--ember);
}

.doc-err {
  margin-top: 6px;
  color: var(--rose);
  font-size: 0.8rem;
}

.doc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.pick {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--cream-dim);
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
  color: inherit;
}

.ghost.sm,
.go.sm {
  padding: 8px 12px;
  font-size: 0.82rem;
  border-radius: 10px;
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
