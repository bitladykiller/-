import { defineStore } from "pinia";
import { computed, ref } from "vue";
import {
  createConversation,
  deleteConversation,
  getHealth,
  getUploadStatus,
  listConversations,
  renameConversation,
  streamAgentQuery,
  uploadDocument,
} from "@/api/client";
import type { ChatMessage, ConversationSummary } from "@/api/types";

function uid() {
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export const useChatStore = defineStore("chat", () => {
  const userId = ref(Number(localStorage.getItem("ag_uid") || 1) || 1);
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

  function setUserId(id: number) {
    userId.value = id;
    localStorage.setItem("ag_uid", String(id));
    conversationId.value = null;
    threadId.value = null;
    messages.value = [];
  }

  async function refreshHealth() {
    healthy.value = await getHealth();
  }

  async function refreshConversations() {
    conversations.value = await listConversations(userId.value);
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

  function selectConversation(c: ConversationSummary) {
    abort?.abort();
    abort = null;
    conversationId.value = c.id;
    threadId.value = String(c.id);
    messages.value = [
      {
        id: uid(),
        role: "system",
        content:
          "历史消息保存在服务端短期记忆中；此界面从本次输入起展示对话内容。",
        createdAt: Date.now(),
      },
    ];
    streaming.value = false;
    statusLine.value = `已切换：${c.title}`;
    error.value = null;
  }

  async function removeConversation(id: number) {
    await deleteConversation(id, userId.value);
    if (conversationId.value === id) newChat();
    await refreshConversations();
  }

  async function ensureSession(firstQuery: string) {
    if (conversationId.value && threadId.value) return;
    const { conversation_id } = await createConversation(userId.value);
    conversationId.value = conversation_id;
    threadId.value = String(conversation_id);
    const title =
      firstQuery.trim().slice(0, 28) +
      (firstQuery.trim().length > 28 ? "…" : "");
    try {
      await renameConversation(conversation_id, userId.value, title || "新对话");
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
          userId: userId.value,
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
    const accepted = await uploadDocument(userId.value, file, options);
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
      if (st.status === "failed") {
        throw new Error(st.error || "索引失败");
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error("轮询超时");
  }

  return {
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
    setUserId,
    refreshHealth,
    refreshConversations,
    newChat,
    selectConversation,
    removeConversation,
    send,
    upload,
  };
});
