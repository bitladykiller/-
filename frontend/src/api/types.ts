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
  title?: string;
  size?: number;
  path?: string;
  message?: string;
  doc_id?: string;
  mode?: string;
  content_hash?: string;
}

export interface TaskStatusPayload {
  task_id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  updated_at?: string;
  result?: {
    status?: string;
    chunks?: number;
    doc_id?: string;
    version?: number;
    soft_deleted?: number;
    message?: string;
  };
  error?: string;
}

/** MySQL user_documents 列表项 */
export interface UserDocumentSummary {
  id: number;
  user_id: number;
  doc_id: string;
  title: string;
  original_name: string;
  source_path?: string;
  content_hash?: string;
  status: string;
  version: number;
  chunk_count: number;
  last_task_id?: string;
  error_message?: string;
  created_at?: string;
  updated_at?: string;
}

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  streaming?: boolean;
  createdAt: number;
}
