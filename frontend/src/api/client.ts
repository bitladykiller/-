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

// ---------------------------------------------------------------------- //
// 认证：token 是唯一身份来源（v3.35.0 起后端不再接受自报 user_id）
// ---------------------------------------------------------------------- //

const TOKEN_KEY = "ag_token";

export type AuthInfo = {
  access_token: string;
  token_type: string;
  user_id: number;
  username: string;
};

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

/** 401 时抛出，供上层区分"要重新登录"和普通错误 */
export class AuthRequiredError extends Error {}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

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

async function ensureOk(res: Response): Promise<Response> {
  if (res.status === 401) {
    setToken(null);
    throw new AuthRequiredError(await parseError(res));
  }
  if (!res.ok) throw new Error(await parseError(res));
  return res;
}

export async function register(username: string, password: string): Promise<AuthInfo> {
  const res = await fetch(`${BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  await ensureOk(res);
  const info: AuthInfo = await res.json();
  setToken(info.access_token);
  return info;
}

export async function login(username: string, password: string): Promise<AuthInfo> {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  await ensureOk(res);
  const info: AuthInfo = await res.json();
  setToken(info.access_token);
  return info;
}

/** 启动时校验本地 token 是否仍有效 */
export async function fetchMe(): Promise<{ user_id: number; username: string }> {
  const res = await fetch(`${BASE}/api/auth/me`, { headers: authHeaders() });
  await ensureOk(res);
  return res.json();
}

// ---------------------------------------------------------------------- //
// 业务接口（全部携带 Bearer token）
// ---------------------------------------------------------------------- //

export async function getHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await fetch(`${BASE}/api/conversations`, { headers: authHeaders() });
  await ensureOk(res);
  return res.json();
}

export async function createConversation(): Promise<CreateConversationResponse> {
  const res = await fetch(`${BASE}/api/conversations`, {
    method: "POST",
    headers: authHeaders(),
  });
  await ensureOk(res);
  return res.json();
}

export async function renameConversation(
  id: number,
  name: string,
): Promise<MessageResponse> {
  const res = await fetch(`${BASE}/api/conversations/${id}/name`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ name }),
  });
  await ensureOk(res);
  return res.json();
}

export async function deleteConversation(id: number): Promise<MessageResponse> {
  const res = await fetch(`${BASE}/api/conversations/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  await ensureOk(res);
  return res.json();
}

export type HistoryMessage = { role: string; content: string; created_at: string };

/** 持久化历史（MySQL messages），与 STM 推理上下文互相独立 */
export async function listConversationMessages(
  id: number,
): Promise<HistoryMessage[]> {
  const res = await fetch(`${BASE}/api/conversations/${id}/messages`, {
    headers: authHeaders(),
  });
  await ensureOk(res);
  return res.json();
}

export async function listDocuments(): Promise<UserDocumentSummary[]> {
  const res = await fetch(`${BASE}/api/documents`, { headers: authHeaders() });
  await ensureOk(res);
  return res.json();
}

export async function getDocument(docId: string): Promise<UserDocumentSummary> {
  const res = await fetch(
    `${BASE}/api/documents/${encodeURIComponent(docId)}`,
    { headers: authHeaders() },
  );
  await ensureOk(res);
  return res.json();
}

/** 删除知识文档：MySQL 元信息删行 + Milvus 软删该 doc_id 全部 chunk */
export async function deleteDocument(
  docId: string,
): Promise<MessageResponse & { doc_id: string; soft_deleted_chunks: number }> {
  const res = await fetch(
    `${BASE}/api/documents/${encodeURIComponent(docId)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  await ensureOk(res);
  return res.json();
}

export async function uploadDocument(
  file: File,
  options?: {
    mode?: "create" | "replace";
    docId?: string;
    visibility?: "global" | "private";
  },
): Promise<UploadAccepted> {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", options?.mode ?? "create");
  if (options?.docId) form.append("doc_id", options.docId);
  if (options?.visibility) form.append("visibility", options.visibility);
  const res = await fetch(`${BASE}/api/upload`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  await ensureOk(res);
  return res.json();
}

export async function getUploadStatus(taskId: string): Promise<TaskStatusPayload> {
  const res = await fetch(`${BASE}/api/upload/status/${taskId}`, {
    headers: authHeaders(),
  });
  await ensureOk(res);
  return res.json();
}

// ---------------------------------------------------------------------- //
// SSE 流式问答
// ---------------------------------------------------------------------- //

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

/** SSE 流式问答（conversation_id 为服务端会话主键；缺省自动创建） */
export async function streamAgentQuery(
  params: {
    query: string;
    conversationId?: string | null;
  },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<string> {
  const form = new FormData();
  form.append("query", params.query);
  if (params.conversationId) {
    form.append("conversation_id", params.conversationId);
  }

  const res = await fetch(`${BASE}/api/langgraph/query`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
    signal,
  });

  await ensureOk(res);

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
