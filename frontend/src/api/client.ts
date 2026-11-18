import { ApiError, responseError } from './errors';
import { redactSensitiveValue } from './safety';
import type { AgentResult, ConfirmResult, Draft, DraftFields, Health, SessionDetail, SessionSummary, Ticket } from './types';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
export interface RequestOptions { method?: string; body?: unknown; timeoutMs?: number; signal?: AbortSignal }
export async function fetchJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, options.timeoutMs ?? (method === 'GET' ? 5000 : 100000));
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method, signal: controller.signal, headers: { Accept: 'application/json', ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
    });
    let payload: unknown;
    try { payload = await response.json(); }
    catch { throw new ApiError('invalid_response', '服务返回内容无法解析，请刷新后重试。', response.status); }
    if (!response.ok) throw responseError(response.status, payload);
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new ApiError('invalid_response', '服务返回格式不正确，请刷新后重试。');
    return redactSensitiveValue(payload) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new ApiError('request_timeout', '服务响应超时。请求可能仍在处理，请先刷新会话，再使用原请求重试。');
    throw new ApiError('network_error', '无法连接问答服务，请确认 FastAPI 已启动后重试。');
  } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); }
}
export const api = {
  health: () => fetchJson<Health>('/api/health'),
  listSessions: (offset = 0, limit = 10) => fetchJson<{ sessions: SessionSummary[] }>(`/api/sessions?limit=${limit}&offset=${offset}`),
  getSession: (id: string) => fetchJson<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`),
  createSession: () => fetchJson<SessionSummary>('/api/sessions', { method: 'POST', body: {} }),
  renameSession: (id: string, title: string) => fetchJson<SessionSummary>(`/api/sessions/${encodeURIComponent(id)}`, { method: 'PATCH', body: { title } }),
  deleteSession: (id: string) => fetchJson<{ status: string }>(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE', body: {} }),
  sendMessage: (id: string, question: string, requestId: string) => fetchJson<AgentResult>(`/api/sessions/${encodeURIComponent(id)}/messages`, { method: 'POST', body: { question, request_id: requestId } }),
  listTickets: (limit = 20, offset = 0) => fetchJson<{ tickets: Ticket[] }>(`/api/tickets?limit=${limit}&offset=${offset}`),
  getTicket: (id: string) => fetchJson<Ticket>(`/api/tickets/${encodeURIComponent(id)}`),
  getDraft: (id: string) => fetchJson<Draft>(`/api/drafts/${encodeURIComponent(id)}`),
  updateDraft: (id: string, fields: DraftFields) => fetchJson<{ status: string; draft: Draft }>(`/api/drafts/${encodeURIComponent(id)}`, { method: 'PATCH', body: fields }),
  confirmDraft: (id: string) => fetchJson<ConfirmResult>(`/api/drafts/${encodeURIComponent(id)}/confirm`, { method: 'POST', body: {} }),
  cancelDraft: (id: string) => fetchJson<{ status: string; draft: Draft }>(`/api/drafts/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: {} }),
};
