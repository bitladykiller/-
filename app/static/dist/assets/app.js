/**
 * AssistGen 前端 — 对接后端 /api/*
 * 会话 CRUD · SSE 流式问答 · 文档上传轮询
 */

const API = ""; // 同源挂载

/** @typedef {{ id: number, title: string, created_at: string, status: string, dialogue_type: string }} Conv */
/** @typedef {{ role: 'user'|'assistant', content: string, streaming?: boolean }} Msg */

const state = {
  userId: Number(localStorage.getItem("ag_user_id") || 1) || 1,
  /** @type {number|null} 当前 MySQL 会话 id */
  conversationId: null,
  /** @type {string|null} 记忆用 thread_id（优先用 X-Conversation-ID） */
  threadId: null,
  /** @type {Conv[]} */
  conversations: [],
  /** @type {Msg[]} */
  messages: [],
  streaming: false,
  /** @type {File|null} */
  pendingFile: null,
};

// ---------- DOM ----------
const $ = (id) => document.getElementById(id);
const els = {
  app: $("app"),
  sidebar: $("sidebar"),
  userIdInput: $("userIdInput"),
  healthDot: $("healthDot"),
  healthText: $("healthText"),
  convList: $("convList"),
  convEmpty: $("convEmpty"),
  btnNewChat: $("btnNewChat"),
  btnRefresh: $("btnRefresh"),
  btnToggleSidebar: $("btnToggleSidebar"),
  btnUpload: $("btnUpload"),
  btnDeleteChat: $("btnDeleteChat"),
  btnSend: $("btnSend"),
  input: $("input"),
  messages: $("messages"),
  welcome: $("welcome"),
  chatTitle: $("chatTitle"),
  chatSubtitle: $("chatSubtitle"),
  composerHint: $("composerHint"),
  uploadModal: $("uploadModal"),
  dropzone: $("dropzone"),
  fileInput: $("fileInput"),
  dropFileName: $("dropFileName"),
  btnDoUpload: $("btnDoUpload"),
  uploadProgress: $("uploadProgress"),
  uploadBar: $("uploadBar"),
  uploadStatus: $("uploadStatus"),
  toastHost: $("toastHost"),
};

// ---------- Utils ----------
function toast(msg, type = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  els.toastHost.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity .25s";
    setTimeout(() => el.remove(), 250);
  }, 3200);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 轻量 Markdown：粗体、行内代码、链接、换行 */
function formatContent(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(
    /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>',
  );
  return s;
}

function autoResize(ta) {
  ta.style.height = "auto";
  ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
}

function setHint(text) {
  els.composerHint.textContent = text;
}

function getUserId() {
  const n = Number(els.userIdInput.value);
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : 1;
}

// ---------- API ----------
async function apiJson(path, options = {}) {
  const res = await fetch(`${API}${path}`, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  return res.json();
}

async function checkHealth() {
  try {
    const r = await fetch(`${API}/health`);
    const ok = r.ok;
    els.healthDot.classList.toggle("ok", ok);
    els.healthDot.classList.toggle("bad", !ok);
    els.healthText.textContent = ok ? "后端在线" : "后端异常";
    return ok;
  } catch {
    els.healthDot.classList.remove("ok");
    els.healthDot.classList.add("bad");
    els.healthText.textContent = "无法连接后端";
    return false;
  }
}

async function loadConversations() {
  const userId = getUserId();
  state.userId = userId;
  localStorage.setItem("ag_user_id", String(userId));
  try {
    state.conversations = await apiJson(`/api/conversations/user/${userId}`);
  } catch (e) {
    state.conversations = [];
    toast(`加载会话失败：${e.message}`, "err");
  }
  renderConvList();
}

function renderConvList() {
  const list = state.conversations || [];
  els.convList.innerHTML = "";
  els.convEmpty.hidden = list.length > 0;

  for (const c of list) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "conv-item" + (c.id === state.conversationId ? " active" : "");
    btn.innerHTML = `
      <span class="title">${escapeHtml(c.title || "会话")}</span>
      <span class="meta">${escapeHtml(String(c.created_at || "").replace("T", " ").slice(0, 16))}</span>
    `;
    btn.addEventListener("click", () => selectConversation(c));
    li.appendChild(btn);
    els.convList.appendChild(li);
  }
}

async function createConversation() {
  const userId = getUserId();
  const data = await apiJson("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  return data.conversation_id;
}

async function renameConversation(id, name) {
  await apiJson(`/api/conversations/${id}/name`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

async function deleteConversation(id) {
  await apiJson(`/api/conversations/${id}`, { method: "DELETE" });
}

// ---------- Chat UI ----------
function resetChatView() {
  state.messages = [];
  state.conversationId = null;
  state.threadId = null;
  els.messages.innerHTML = "";
  els.messages.hidden = true;
  els.welcome.hidden = false;
  els.chatTitle.textContent = "智能客服";
  els.chatSubtitle.textContent = "支持图谱检索 · 文档 RAG · 分层记忆";
  els.btnDeleteChat.disabled = true;
  updateSendEnabled();
  renderConvList();
}

function showMessages() {
  els.welcome.hidden = true;
  els.messages.hidden = false;
}

function selectConversation(c) {
  state.conversationId = c.id;
  // 列表里的会话用 MySQL id 作 thread，与删会话清记忆一致
  state.threadId = String(c.id);
  state.messages = [];
  els.messages.innerHTML = "";
  showMessages();
  els.chatTitle.textContent = c.title || `会话 #${c.id}`;
  els.chatSubtitle.textContent = `会话 ID ${c.id} · 续聊将复用记忆`;
  els.btnDeleteChat.disabled = false;
  renderConvList();
  setHint(`已切换到「${c.title}」— 输入继续提问`);
  els.app.classList.remove("sidebar-open");
  // 列表接口不返回历史消息（消息在 Redis STM），本地仅展示本页新消息
  appendSystemNote("历史消息存于服务端短期记忆；本页从本次输入起展示。");
}

function appendSystemNote(text) {
  const row = document.createElement("div");
  row.className = "msg assistant";
  row.innerHTML = `
    <div class="avatar">i</div>
    <div>
      <div class="bubble" style="opacity:.85;font-size:.88rem">${escapeHtml(text)}</div>
    </div>`;
  els.messages.appendChild(row);
  els.messages.scrollTop = els.messages.scrollHeight;
}

/**
 * @param {'user'|'assistant'} role
 * @param {string} content
 * @param {{streaming?: boolean}} [opts]
 */
function appendMessage(role, content, opts = {}) {
  showMessages();
  const msg = { role, content, streaming: !!opts.streaming };
  state.messages.push(msg);

  const row = document.createElement("div");
  row.className = `msg ${role}`;
  row.dataset.idx = String(state.messages.length - 1);

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "我" : "AI";

  const wrap = document.createElement("div");
  const bubble = document.createElement("div");
  bubble.className = "bubble" + (opts.streaming ? " streaming" : "");
  if (opts.streaming && !content) {
    bubble.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  } else {
    bubble.innerHTML = formatContent(content);
  }
  wrap.appendChild(bubble);
  row.appendChild(avatar);
  row.appendChild(wrap);
  els.messages.appendChild(row);
  els.messages.scrollTop = els.messages.scrollHeight;
  return bubble;
}

function updateLastAssistant(content, streaming) {
  const idx = state.messages.length - 1;
  if (idx < 0 || state.messages[idx].role !== "assistant") return;
  state.messages[idx].content = content;
  state.messages[idx].streaming = streaming;
  const row = els.messages.querySelector(`.msg[data-idx="${idx}"] .bubble`);
  if (!row) return;
  row.classList.toggle("streaming", !!streaming);
  row.innerHTML = formatContent(content) || (streaming ? '<span class="typing"><i></i><i></i><i></i></span>' : "");
  els.messages.scrollTop = els.messages.scrollHeight;
}

function updateSendEnabled() {
  const hasText = els.input.value.trim().length > 0;
  els.btnSend.disabled = !hasText || state.streaming;
}

// ---------- SSE ----------
/**
 * 解析 SSE 缓冲，返回 { events: string[], rest: string }
 */
function parseSseBuffer(buffer) {
  const events = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const block of parts) {
    const lines = block.split("\n");
    for (const line of lines) {
      if (line.startsWith("data:")) {
        const raw = line.slice(5).trim();
        if (!raw) continue;
        try {
          events.push(JSON.parse(raw));
        } catch {
          events.push(raw);
        }
      }
    }
  }
  return { events, rest };
}

async function streamQuery(query) {
  const form = new FormData();
  form.append("query", query);
  form.append("user_id", String(getUserId()));
  if (state.threadId) {
    form.append("conversation_id", state.threadId);
  }

  const res = await fetch(`${API}/api/langgraph/query`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const b = await res.json();
      detail = b.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  const headerId = res.headers.get("X-Conversation-ID");
  if (headerId) {
    state.threadId = headerId;
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error("浏览器不支持流式读取");

  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";

  appendMessage("assistant", "", { streaming: true });

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseBuffer(buffer);
    buffer = rest;
    for (const ev of events) {
      const piece = typeof ev === "string" ? ev : String(ev);
      full += piece;
      updateLastAssistant(full, true);
    }
  }

  // 尾部残留
  if (buffer.trim()) {
    const { events } = parseSseBuffer(buffer + "\n\n");
    for (const ev of events) {
      full += typeof ev === "string" ? ev : String(ev);
    }
  }
  updateLastAssistant(full || "（无返回内容）", false);
  return full;
}

async function ensureConversationForSend(firstQuery) {
  if (state.conversationId && state.threadId) return;

  setHint("创建会话中…");
  const id = await createConversation();
  state.conversationId = id;
  state.threadId = String(id);

  const title =
    firstQuery.trim().slice(0, 24) + (firstQuery.trim().length > 24 ? "…" : "");
  try {
    await renameConversation(id, title || "新对话");
    els.chatTitle.textContent = title || "新对话";
  } catch {
    els.chatTitle.textContent = `会话 #${id}`;
  }
  els.chatSubtitle.textContent = `会话 ID ${id}`;
  els.btnDeleteChat.disabled = false;
  await loadConversations();
}

async function sendMessage(raw) {
  const query = (raw ?? els.input.value).trim();
  if (!query || state.streaming) return;

  state.streaming = true;
  updateSendEnabled();
  els.input.value = "";
  autoResize(els.input);

  try {
    await ensureConversationForSend(query);
    appendMessage("user", query);
    setHint("思考与检索中…");
    await streamQuery(query);
    setHint("完成 — 可继续提问");
  } catch (e) {
    toast(e.message || String(e), "err");
    setHint("出错了，请重试");
    // 若 assistant 空气泡，补错误
    const last = state.messages[state.messages.length - 1];
    if (last?.role === "assistant" && !last.content) {
      updateLastAssistant(`请求失败：${e.message}`, false);
    } else if (!last || last.role === "user") {
      appendMessage("assistant", `请求失败：${e.message}`);
    }
  } finally {
    state.streaming = false;
    updateSendEnabled();
  }
}

// ---------- Upload ----------
function openUpload() {
  els.uploadModal.hidden = false;
  state.pendingFile = null;
  els.dropFileName.textContent = "";
  els.btnDoUpload.disabled = true;
  els.uploadProgress.hidden = true;
  els.uploadBar.style.width = "0%";
  els.uploadStatus.textContent = "等待上传…";
}

function closeUpload() {
  els.uploadModal.hidden = true;
  els.fileInput.value = "";
  state.pendingFile = null;
}

function setPendingFile(file) {
  if (!file) return;
  const ok = /\.(md|markdown|pdf|docx)$/i.test(file.name);
  if (!ok) {
    toast("仅支持 .md / .markdown / .pdf / .docx", "err");
    return;
  }
  state.pendingFile = file;
  els.dropFileName.textContent = file.name;
  els.btnDoUpload.disabled = false;
}

async function pollTask(taskId) {
  const max = 120;
  for (let i = 0; i < max; i++) {
    const st = await apiJson(`/api/upload/status/${taskId}`);
    const status = st?.status || "unknown";
    const pct = status === "pending" ? 15 : status === "running" ? 55 : 100;
    els.uploadBar.style.width = `${pct}%`;
    els.uploadStatus.textContent = `任务 ${status}` + (st?.error ? ` · ${st.error}` : "");

    if (status === "completed") {
      const chunks = st?.result?.chunks ?? st?.result?.chunks;
      const n = st?.result?.chunks;
      toast(
        typeof n === "number" ? `索引完成，${n} 个片段` : "索引完成",
        "ok",
      );
      return st;
    }
    if (status === "failed") {
      throw new Error(st?.error || "索引失败");
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("轮询超时");
}

async function doUpload() {
  if (!state.pendingFile) return;
  els.btnDoUpload.disabled = true;
  els.uploadProgress.hidden = false;
  els.uploadBar.style.width = "8%";
  els.uploadStatus.textContent = "上传中…";

  try {
    const form = new FormData();
    form.append("file", state.pendingFile);
    form.append("user_id", String(getUserId()));
    const res = await fetch(`${API}/api/upload`, { method: "POST", body: form });
    if (!res.ok) {
      let d = res.statusText;
      try {
        const b = await res.json();
        d = b.detail || d;
      } catch {
        /* ignore */
      }
      throw new Error(d);
    }
    const data = await res.json();
    els.uploadBar.style.width = "20%";
    els.uploadStatus.textContent = `已接收 task=${data.task_id}，解析中…`;
    await pollTask(data.task_id);
    els.uploadBar.style.width = "100%";
    closeUpload();
  } catch (e) {
    toast(e.message || String(e), "err");
    els.uploadStatus.textContent = e.message;
    els.btnDoUpload.disabled = false;
  }
}

// ---------- Events ----------
function bindEvents() {
  els.userIdInput.value = String(state.userId);

  els.userIdInput.addEventListener("change", () => {
    resetChatView();
    loadConversations();
  });

  els.btnNewChat.addEventListener("click", () => {
    resetChatView();
    setHint("新对话 — 发送首条消息时自动创建会话");
    els.input.focus();
    els.app.classList.remove("sidebar-open");
  });

  els.btnRefresh.addEventListener("click", () => loadConversations());

  els.btnToggleSidebar.addEventListener("click", () => {
    els.app.classList.toggle("sidebar-open");
  });

  els.btnDeleteChat.addEventListener("click", async () => {
    if (!state.conversationId) return;
    if (!confirm("删除该会话并清理关联记忆？")) return;
    try {
      await deleteConversation(state.conversationId);
      toast("会话已删除", "ok");
      resetChatView();
      await loadConversations();
    } catch (e) {
      toast(e.message, "err");
    }
  });

  els.input.addEventListener("input", () => {
    autoResize(els.input);
    updateSendEnabled();
  });

  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  els.btnSend.addEventListener("click", () => sendMessage());

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const q = chip.getAttribute("data-q") || "";
      sendMessage(q);
    });
  });

  els.btnUpload.addEventListener("click", openUpload);
  els.uploadModal.querySelectorAll("[data-close]").forEach((el) => {
    el.addEventListener("click", closeUpload);
  });

  els.dropzone.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => {
    const f = els.fileInput.files?.[0];
    if (f) setPendingFile(f);
  });

  ["dragenter", "dragover"].forEach((ev) => {
    els.dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    els.dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("dragover");
    });
  });
  els.dropzone.addEventListener("drop", (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) setPendingFile(f);
  });

  els.btnDoUpload.addEventListener("click", doUpload);
}

// ---------- Boot ----------
async function boot() {
  bindEvents();
  updateSendEnabled();
  await checkHealth();
  await loadConversations();
  setInterval(checkHealth, 30000);
}

boot();
