export interface ConversationSummary {
  id: number;
  title: string;
  created_at: string;
  status: string;
  dialogue_type: string;
}

export interface CreateConversationResponse {
  conversation_id: number;
}

export interface MessageResponse {
  message: string;
}

export interface UploadAccepted {
  task_id: string;
  filename?: string;
  original_name?: string;
  size?: number;
  path?: string;
  message?: string;
}

export interface TaskStatusPayload {
  task_id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  updated_at?: string;
  result?: {
    status?: string;
    chunks?: number;
    doc_id?: string;
    message?: string;
  };
  error?: string;
}

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  streaming?: boolean;
  createdAt: number;
}
