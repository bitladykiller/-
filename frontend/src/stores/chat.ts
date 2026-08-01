import { defineStore } from "pinia";
import { computed, ref } from "vue";
import {
  AuthRequiredError,
  createConversation,
  deleteConversation,
  fetchMe,
  getHealth,
  getToken,
  getUploadStatus,
  listConversationMessages,
  listConversations,
  login as apiLogin,
  register as apiRegister,
  renameConversation,
  setToken,
  streamAgentQuery,
  uploadDocument,
} from "@/api/client";
import type { ChatMessage, ConversationSummary } from "@/api/types";

function uid() {
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export const useChatStore = defineStore("chat", () => {
  // ---- 认证状态：身份来自后端令牌，不再由前端自报 uid ----
  const authenticated = ref(false);
  const authChecking = ref(true);
  const username = ref<string>("");
  const userId = ref<number | null>(null);

  const conversations = ref<ConversationSummary[]>([]);
  const conversationId = ref<number | null>(null);
  const threadId = ref<string | null>(null);
  const messages = ref<ChatMessage[]>([]);
  const streaming = ref(false);
  const healthy = ref<boolean | null>(null);
  const statusLine = ref("准备就绪");
  const error = ref<string | null>(null);

  let abort: AbortController | null = null;

  const activeTitle = computed(() => {
    if (!conversationId.value) return "新对话";
    const hit = conversations.value.find((c) => c.id === conversationId.value);
    return hit?.title || `会话 #${conversationId.value}`;
  });

  function resetWorkspace() {
    abort?.abort();
    abort = null;
    conversations.value = [];
    conversationId.value = null;
    threadId.value = null;
    messages.value = [];
    streaming.value = false;
    error.value = null;
  }

  function handleAuthLoss(message?: string) {
    authenticated.value = false;
    username.value = "";
    userId.value = null;
    resetWorkspace();
    statusLine.value = message || "登录已过期，请重新登录";
  }

  async function bootstrapAuth() {
    authChecking.value = true;
    try {
      if (!getToken()) return;
      const me = await fetchMe();
      authenticated.value = true;
      username.value = me.username;
      userId.value = me.user_id;
      await refreshConversations();
    } catch {
      setToken(null);
    } finally {
      authChecking.value = false;
    }
  }

  async function login(name: string, password: string) {
    const info = await apiLogin(name, password);
    authenticated.value = true;
    username.value = info.username;
    userId.value = info.user_id;
    statusLine.value = `欢迎回来，${info.username}`;
    await refreshConversations();
  }

  async function registerAccount(name: string, password: string) {
    const info = await apiRegister(name, password);
    authenticated.value = true;
    username.value = info.username;
    userId.value = info.user_id;
    statusLine.value = `注册成功，${info.username}`;
    await refreshConversations();
  }

  function logout() {
    setToken(null);
    handleAuthLoss("已退出登录");
  }

  async function refreshHealth() {
    healthy.value = await getHealth();
  }

  async function refreshConversations() {
    try {
      conversations.value = await listConversations();
    } catch (e) {
      if (e instanceof AuthRequiredError) handleAuthLoss();
      else throw e;
    }
  }

  function newChat() {
    abort?.abort();
    abort = null;
    conversationId.value = null;
    threadId.value = null;
    messages.value = [];
    streaming.value = false;
    statusLine.value = "新对话 — 发送首条消息时创建会话";
    error.value = null;
  }

  async function selectConversation(c: ConversationSummary) {
    abort?.abort();
    abort = null;
    conversationId.value = c.id;
    threadId.value = String(c.id);
    streaming.value = false;
    error.value = null;
    statusLine.value = `已切换：${c.title}`;

    // 加载持久化历史（MySQL messages）：切回旧会话不再"隔天失忆"
    messages.value = [];
    try {
      const history = await listConversationMessages(c.id);
      messages.value = history.map((m) => ({
        id: uid(),
        role: m.role === "assistant" ? "assistant" : "user",
        content: m.content,
        createdAt: Date.parse(m.created_at) || Date.now(),
      }));
      if (!history.length) {
        messages.value = [
          {
            id: uid(),
            role: "system",
            content: "该会话暂无历史消息。",
            createdAt: Date.now(),
          },
        ];
      }
    } catch (e) {
      if (e instanceof AuthRequiredError) return handleAuthLoss();
      messages.value = [
        {
          id: uid(),
          role: "system",
          content: "历史消息加载失败，可直接继续对话。",
          createdAt: Date.now(),
        },
      ];
    }
  }

  async function removeConversation(id: number) {
    try {
      await deleteConversation(id);
    } catch (e) {
      if (e instanceof AuthRequiredError) return handleAuthLoss();
      throw e;
    }
    if (conversationId.value === id) newChat();
    await refreshConversations();
  }

  async function ensureSession(firstQuery: string) {
    if (conversationId.value && threadId.value) return;
    const { conversation_id } = await createConversation();
    conversationId.value = conversation_id;
    threadId.value = String(conversation_id);
    const title =
      firstQuery.trim().slice(0, 28) +
      (firstQuery.trim().length > 28 ? "…" : "");
    try {
      await renameConversation(conversation_id, title || "新对话");
    } catch {
      /* 列表可能暂时不显示未改名会话 */
    }
    await refreshConversations();
  }

  async function send(text: string) {
    const query = text.trim();
    if (!query || streaming.value) return;

    error.value = null;
    streaming.value = true;
    statusLine.value = "创建会话 / 检索中…";
    abort = new AbortController();

    try {
      await ensureSession(query);
      messages.value.push({
        id: uid(),
        role: "user",
        content: query,
        createdAt: Date.now(),
      });

      const assistantId = uid();
      messages.value.push({
        id: assistantId,
        role: "assistant",
        content: "",
        streaming: true,
        createdAt: Date.now(),
      });

      const full = await streamAgentQuery(
        {
          query,
          conversationId: threadId.value,
        },
        {
          onChunk: (t) => {
            const m = messages.value.find((x) => x.id === assistantId);
            if (m) {
              m.content = t;
              m.streaming = true;
            }
          },
          onThreadId: (id) => {
            threadId.value = id;
            const numeric = Number(id);
            if (Number.isFinite(numeric)) conversationId.value = numeric;
          },
        },
        abort.signal,
      );

      const m = messages.value.find((x) => x.id === assistantId);
      if (m) {
        m.content = full || "（无返回内容）";
        m.streaming = false;
      }
      statusLine.value = "完成";
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        statusLine.value = "已取消";
        return;
      }
      if (e instanceof AuthRequiredError) return handleAuthLoss();
      const msg = e instanceof Error ? e.message : String(e);
      error.value = msg;
      statusLine.value = "出错";
      const last = messages.value[messages.value.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        last.content = last.content || `请求失败：${msg}`;
        last.streaming = false;
      } else {
        messages.value.push({
          id: uid(),
          role: "assistant",
          content: `请求失败：${msg}`,
          createdAt: Date.now(),
        });
      }
    } finally {
      streaming.value = false;
      abort = null;
    }
  }

  async function upload(
    file: File,
    onProgress?: (label: string, pct: number) => void,
    options?: { mode?: "create" | "replace"; docId?: string },
  ) {
    onProgress?.("上传中…", 10);
    const accepted = await uploadDocument(file, options);
    if (accepted.unchanged || accepted.skipped || !accepted.task_id) {
      onProgress?.(accepted.message || "内容未变化，已跳过 reindex", 100);
      return {
        task_id: accepted.task_id || "",
        status: "completed",
        result: {
          status: "success",
          doc_id: accepted.doc_id,
          chunks: accepted.chunk_count,
          version: accepted.version,
          message: accepted.message,
        },
      };
    }
    onProgress?.(
      `解析中 task=${accepted.task_id}${accepted.doc_id ? ` · ${accepted.doc_id}` : ""}`,
      25,
    );

    for (let i = 0; i < 120; i++) {
      const st = await getUploadStatus(accepted.task_id);
      if (st.status === "pending") onProgress?.("排队中…", 30);
      if (st.status === "running") onProgress?.("解析索引中…", 60);
      if (st.status === "completed") {
        const n = st.result?.chunks;
        const doc = st.result?.doc_id || accepted.doc_id;
        onProgress?.(
          typeof n === "number"
            ? `完成，${n} 个片段${doc ? ` · ${doc}` : ""}`
            : "索引完成",
          100,
        );
        return st;
      }
      if (st.status === "failed" || st.status === "interrupted") {
        throw new Error(st.error || "索引失败");
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error("轮询超时");
  }

  return {
    authenticated,
    authChecking,
    username,
    userId,
    conversations,
    conversationId,
    threadId,
    messages,
    streaming,
    healthy,
    statusLine,
    error,
    activeTitle,
    bootstrapAuth,
    login,
    registerAccount,
    logout,
    refreshHealth,
    refreshConversations,
    newChat,
    selectConversation,
    removeConversation,
    send,
    upload,
  };
});
