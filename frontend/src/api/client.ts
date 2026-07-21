import type {
  ConversationSummary,
  CreateConversationResponse,
  MessageResponse,
  TaskStatusPayload,
  UploadAccepted,
} from "./types";

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
  name: string,
): Promise<MessageResponse> {
  const res = await fetch(`${BASE}/api/conversations/${id}/name`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteConversation(id: number): Promise<MessageResponse> {
  const res = await fetch(`${BASE}/api/conversations/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function uploadDocument(
  userId: number,
  file: File,
): Promise<UploadAccepted> {
  const form = new FormData();
  form.append("user_id", String(userId));
  form.append("file", file);
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

function parseSseBuffer(buffer: string): { events: string[]; rest: string } {
  const events: string[] = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const block of parts) {
    for (const line of block.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      try {
        const parsed = JSON.parse(raw);
        events.push(typeof parsed === "string" ? parsed : String(parsed));
      } catch {
        events.push(raw);
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

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseBuffer(buffer);
    buffer = rest;
    for (const piece of events) {
      full += piece;
      handlers.onChunk(full);
    }
  }

  if (buffer.trim()) {
    const { events } = parseSseBuffer(`${buffer}\n\n`);
    for (const piece of events) {
      full += piece;
      handlers.onChunk(full);
    }
  }

  return full;
}
