<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import {
  listDocuments,
  type UserDocumentSummary,
  uploadDocument,
  getUploadStatus,
} from "@/api/client";
const open = defineModel<boolean>("open", { required: true });

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
    docs.value = await listDocuments();
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
    const accepted = await uploadDocument(file.value, {
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
    const accepted = await uploadDocument(replaceFile.value, {
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
    <div class="sheet plaque deco-corners" role="dialog" aria-label="知识文档">
      <div class="sheet-rays" aria-hidden="true" />
      <header>
        <div>
          <p class="eyebrow">◆ Knowledge Ingest</p>
          <h2>知识文档</h2>
        </div>
        <button type="button" class="x" aria-label="关闭" @click="open = false">×</button>
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
          <span class="zone-mark" aria-hidden="true"><i>↑</i></span>
          <span class="zone-title">拖入文件或点击选择</span>
          <span v-if="file" class="zone-file">{{ file.name }}</span>
        </label>

        <footer>
          <button type="button" class="ghost-btn" @click="open = false">取消</button>
          <button
            type="button"
            class="gold-btn"
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
          <button
            type="button"
            class="ghost-btn sm"
            :disabled="loadingDocs"
            @click="refreshDocs"
          >
            {{ loadingDocs ? "刷新中…" : "刷新列表" }}
          </button>
        </div>

        <div v-if="!docs.length && !loadingDocs" class="empty">
          <span aria-hidden="true">✧</span> 暂无文档，请先上传。
        </div>
        <ul v-else class="doc-list">
          <li v-for="d in docs" :key="d.doc_id" class="doc">
            <div class="doc-main">
              <div class="doc-title">{{ d.title || d.original_name }}</div>
              <div class="doc-meta">
                <span class="mono">{{ d.doc_id }}</span>
                <span>· v{{ d.version }}</span>
                <span>· {{ d.chunk_count }} chunks</span>
                <span class="st" :data-s="d.status">
                  <i class="st-gem" aria-hidden="true" />{{ statusLabel(d.status) }}
                </span>
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
                class="gold-btn sm"
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
        <div class="bar" :class="{ live: busy }">
          <i :style="{ width: pct + '%' }" />
        </div>
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
  background: radial-gradient(120% 120% at 50% 30%, rgba(7, 11, 9, 0.5), rgba(0, 0, 0, 0.72));
  backdrop-filter: blur(9px);
  display: grid;
  place-items: center;
  padding: 20px;
  animation: fade-in 0.2s ease both;
}

.sheet {
  position: relative;
  width: min(580px, 100%);
  max-height: min(86vh, 740px);
  overflow: auto;
  padding: 26px 24px 24px;
  animation: rise-in 0.32s var(--ease) both;
}

/* 穹顶放射纹 */
.sheet-rays {
  position: absolute;
  left: 50%;
  top: 0;
  width: 460px;
  height: 150px;
  transform: translateX(-50%);
  pointer-events: none;
  background: repeating-conic-gradient(
    from -90deg at 50% 0%,
    rgba(216, 178, 106, 0.22) 0deg 0.9deg,
    transparent 0.9deg 7deg
  );
  -webkit-mask-image: radial-gradient(120% 100% at 50% 0%, #000 10%, transparent 62%);
  mask-image: radial-gradient(120% 100% at 50% 0%, #000 10%, transparent 62%);
}

header {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

h2 {
  margin: 6px 0 0;
  font-family: var(--serif);
  font-size: 1.6rem;
  font-weight: 500;
  letter-spacing: 0.02em;
}

.x {
  border: 1px solid transparent;
  background: transparent;
  font-size: 1.35rem;
  cursor: pointer;
  color: var(--ivory-dim);
  line-height: 1;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  transition: border-color 0.15s ease, color 0.15s ease;
}

.x:hover {
  color: var(--ivory);
  border-color: var(--line);
}

/* ---------- 鎏金分段切换 ---------- */
.tabs {
  position: relative;
  display: flex;
  gap: 6px;
  margin: 18px 0 14px;
  padding: 5px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: rgba(7, 11, 9, 0.5);
}

.tabs button {
  flex: 1;
  border-radius: 9px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--ivory-dim);
  padding: 9px 10px;
  cursor: pointer;
  font-size: 0.86rem;
  transition: background 0.16s ease, color 0.16s ease, border-color 0.16s ease;
}

.tabs button.on {
  background: linear-gradient(160deg, var(--gold-soft), rgba(216, 178, 106, 0.05));
  border-color: var(--line-strong);
  color: var(--gold-bright);
  font-weight: 600;
}

.desc {
  margin: 0 0 14px;
  color: var(--ivory-dim);
  font-size: 0.86rem;
  line-height: 1.6;
}

.desc code,
.mono {
  font-family: var(--mono);
  font-size: 0.82em;
  color: var(--gold-bright);
}

/* ---------- 典藏投递口 ---------- */
.zone {
  display: grid;
  place-items: center;
  gap: 10px;
  min-height: 156px;
  padding: 20px;
  border-radius: var(--radius);
  border: 1.5px dashed var(--line-strong);
  background:
    radial-gradient(80% 90% at 50% 0%, rgba(216, 178, 106, 0.05), transparent 70%),
    rgba(7, 11, 9, 0.4);
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease;
}

.zone:hover {
  border-color: rgba(216, 178, 106, 0.6);
  background:
    radial-gradient(80% 90% at 50% 0%, rgba(216, 178, 106, 0.1), transparent 70%),
    rgba(7, 11, 9, 0.4);
}

.zone-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  transform: rotate(45deg);
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--gold-soft);
}

.zone-mark i {
  transform: rotate(-45deg);
  font-style: normal;
  color: var(--gold-bright);
  font-size: 0.95rem;
}

.zone-title {
  color: var(--ivory-dim);
}

.zone-file {
  font-family: var(--mono);
  font-size: 0.78rem;
  color: var(--gold);
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
  padding: 30px 12px;
  text-align: center;
  color: var(--ivory-dim);
  font-size: 0.9rem;
}

.empty span {
  color: var(--gold);
}

/* ---------- 典藏名录 ---------- */
.doc-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}

.doc {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 13px;
  background: rgba(7, 11, 9, 0.4);
  display: grid;
  gap: 10px;
  transition: border-color 0.16s ease, background 0.16s ease;
}

.doc:hover {
  border-color: var(--line-strong);
  background: rgba(216, 178, 106, 0.04);
}

.doc-title {
  font-weight: 600;
  margin-bottom: 5px;
}

.doc-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  font-size: 0.76rem;
  color: var(--ivory-dim);
}

.st {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px solid var(--line-faint);
  background: rgba(216, 178, 106, 0.05);
}

.st-gem {
  width: 6px;
  height: 6px;
  transform: rotate(45deg);
  background: var(--gold-deep);
}

.st[data-s="ready"] {
  color: var(--jade);
}

.st[data-s="ready"] .st-gem {
  background: var(--jade);
  box-shadow: 0 0 6px rgba(142, 201, 168, 0.6);
}

.st[data-s="failed"] {
  color: var(--garnet);
}

.st[data-s="failed"] .st-gem {
  background: var(--garnet);
}

.st[data-s="indexing"],
.st[data-s="pending"] {
  color: var(--gold);
}

.st[data-s="indexing"] .st-gem,
.st[data-s="pending"] .st-gem {
  background: var(--gold);
  animation: gem-pulse 1.6s ease-in-out infinite;
}

.doc-err {
  margin-top: 6px;
  color: var(--garnet);
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
  font-size: 0.8rem;
  color: var(--ivory-dim);
  transition: border-color 0.15s ease, color 0.15s ease;
}

.pick:hover {
  border-color: var(--line-strong);
  color: var(--ivory);
}

/* ---------- 进度鎏金带 ---------- */
.prog {
  margin-top: 16px;
  display: grid;
  gap: 8px;
  font-family: var(--mono);
  font-size: 0.74rem;
  color: var(--ivory-dim);
}

.bar {
  height: 6px;
  border-radius: 99px;
  background: rgba(216, 178, 106, 0.08);
  border: 1px solid var(--line-faint);
  overflow: hidden;
}

.bar i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--gold-deep), var(--gold), var(--gold-bright));
  transition: width 0.25s ease;
}

.bar.live i {
  background: linear-gradient(
    90deg,
    var(--gold-deep) 0%,
    var(--gold) 25%,
    var(--gold-bright) 50%,
    var(--gold) 75%,
    var(--gold-deep) 100%
  );
  background-size: 200% 100%;
  animation: shimmer-sweep 1.6s linear infinite;
}

.err {
  color: var(--garnet);
  font-size: 0.84rem;
  margin: 10px 0 0;
}

footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

.ghost-btn.sm,
.gold-btn.sm {
  padding: 8px 12px;
  font-size: 0.8rem;
  border-radius: 10px;
}
</style>
