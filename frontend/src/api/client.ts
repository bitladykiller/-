import type {
  ConversationSummary,
  CreateConversationResponse,
  MessageResponse,
  TaskStatusPayload,
  UploadAccepted,
  UserDocumentSummary,
} from "./types";

export type { UserDocumentSummary };

/** 开发走 Vite 代理；生产由 nginx 同源反代，默认空字符串 */
const BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (detail != null) return JSON.stringify(detail);
    return JSON.stringify(body);
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

export async function getHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export async function listConversations(
  userId: number,
): Promise<ConversationSummary[]> {
  const res = await fetch(`${BASE}/api/conversations/user/${userId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createConversation(
  userId: number,
): Promise<CreateConversationResponse> {
  const res = await fetch(`${BASE}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function renameConversation(
  id: number,
  userId: number,
  name: string,
): Promise<MessageResponse> {
  const res = await fetch(`${BASE}/api/conversations/${id}/name`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, name }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 删除会话需带归属 user_id：后端会校验，防止按 id 误删他人会话与记忆 */
export async function deleteConversation(
  id: number,
  userId: number,
): Promise<MessageResponse> {
  const res = await fetch(
    `${BASE}/api/conversations/${id}?user_id=${encodeURIComponent(userId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listDocuments(
  userId: number,
): Promise<UserDocumentSummary[]> {
  const res = await fetch(`${BASE}/api/documents/user/${userId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getDocument(
  userId: number,
  docId: string,
): Promise<UserDocumentSummary> {
  const res = await fetch(
    `${BASE}/api/documents/user/${userId}/${encodeURIComponent(docId)}`,
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 删除知识文档：MySQL 元信息删行 + Milvus 软删该 doc_id 全部 chunk */
export async function deleteDocument(
  userId: number,
  docId: string,
): Promise<MessageResponse & { doc_id: string; soft_deleted_chunks: number }> {
  const res = await fetch(
    `${BASE}/api/documents/user/${userId}/${encodeURIComponent(docId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function uploadDocument(
  userId: number,
  file: File,
  options?: { mode?: "create" | "replace"; docId?: string },
): Promise<UploadAccepted> {
  const form = new FormData();
  form.append("user_id", String(userId));
  form.append("file", file);
  form.append("mode", options?.mode ?? "create");
  if (options?.docId) {
    form.append("doc_id", options.docId);
  }
  const res = await fetch(`${BASE}/api/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getUploadStatus(
  taskId: string,
): Promise<TaskStatusPayload> {
  const res = await fetch(`${BASE}/api/upload/status/${taskId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

type SseEvent = { type: "chunk" | "error"; text: string };

function parseSseBuffer(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const block of parts) {
    // 后端用命名事件表达流中途错误：event: error + data: "说明"
    let eventType: SseEvent["type"] = "chunk";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        if (line.slice(6).trim() === "error") eventType = "error";
        continue;
      }
      if (!line.startsWith("data:")) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      try {
        const parsed = JSON.parse(raw);
        events.push({
          type: eventType,
          text: typeof parsed === "string" ? parsed : String(parsed),
        });
      } catch {
        events.push({ type: eventType, text: raw });
      }
    }
  }
  return { events, rest };
}

export type StreamHandlers = {
  onChunk: (text: string) => void;
  onThreadId?: (id: string) => void;
};

/** SSE 流式问答 */
export async function streamAgentQuery(
  params: {
    query: string;
    userId: number;
    conversationId?: string | null;
  },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<string> {
  const form = new FormData();
  form.append("query", params.query);
  form.append("user_id", String(params.userId));
  if (params.conversationId) {
    form.append("conversation_id", params.conversationId);
  }

  const res = await fetch(`${BASE}/api/langgraph/query`, {
    method: "POST",
    body: form,
    signal,
  });

  if (!res.ok) throw new Error(await parseError(res));

  const thread = res.headers.get("X-Conversation-ID");
  if (thread) handlers.onThreadId?.(thread);

  const reader = res.body?.getReader();
  if (!reader) throw new Error("浏览器不支持流式响应");

  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";

  const consume = (events: SseEvent[]) => {
    for (const piece of events) {
      if (piece.type === "error") {
        // 流中途后端异常：已生成的内容保留，错误交给上层展示
        throw new Error(piece.text || "生成过程中出现异常");
      }
      full += piece.text;
      handlers.onChunk(full);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseBuffer(buffer);
    buffer = rest;
    consume(events);
  }

  if (buffer.trim()) {
    const { events } = parseSseBuffer(`${buffer}\n\n`);
    consume(events);
  }

  return full;
}
