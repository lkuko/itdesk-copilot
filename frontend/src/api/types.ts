export type Category = 'vpn' | 'email' | 'device';
export interface Health {
  status: 'ok' | 'not_ready';
  checks?: Record<string, { status?: string; message?: string }>;
  models?: { chat?: string; embedding?: string };
  index?: { document_count?: number; chunk_count?: number; updated_at?: string | null };
  mode?: string;
  agent?: { max_tool_attempts?: number; tools?: number; ticket_write?: string };
}
export interface SessionSummary { session_id: string; title: string; created_at: string; updated_at: string; user_id?: string }
export interface Citation { chunk_id: string; doc_id: string; title: string; source: string; version: string; content: string }
export interface Metrics { retrieval_ms?: number; generation_ms?: number; total_ms?: number; input_tokens?: number; output_tokens?: number; embedding_tokens?: number; cost_usd?: number | null }
export interface ToolTrace { name?: string; status?: string; args?: unknown; result?: unknown; [key: string]: unknown }
export interface AgentResult {
  request_id?: string; run_id?: string;
  status?: 'answered' | 'needs_info' | 'pending_confirmation' | 'no_evidence' | 'failed';
  answer?: string; citations?: Citation[]; tool_trace?: ToolTrace[]; metrics?: Metrics;
  classification?: { category?: string; intent?: string; missing_fields?: string[]; priority?: string };
  error?: { code?: string; message?: string } | null; draft?: Draft | null;
  [key: string]: unknown;
}
export interface Message { message_id?: number | string; role: 'user' | 'assistant'; content: string; created_at?: string; request_id?: string | null; result?: AgentResult | null }
export interface Draft { draft_id: string; session_id: string; user_id?: string; title: string; description: string; category: Category; priority: string; status: 'pending' | 'confirmed' | 'cancelled'; ticket_id?: string | null; created_at?: string; updated_at?: string }
export interface Ticket { ticket_id: string; draft_id?: string | null; user_id?: string; title: string; description: string; category: Category; priority: string; status: string; created_at?: string; updated_at?: string }
export interface SessionDetail extends SessionSummary { messages: Message[]; drafts: Draft[] }
export interface DraftFields { title: string; description: string; category: Category }
export interface ConfirmResult { status: string; ticket: Ticket; draft: Draft }
