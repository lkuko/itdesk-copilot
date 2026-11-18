import type { Page, Route } from '@playwright/test';

const NOW = '2026-11-18T04:00:00Z';
export const SESSION_ID = 'session-00000001';
export const DRAFT_ID = 'draft-00000001';
export interface MockDraft {
  draft_id: string; session_id: string; user_id: string; title: string; description: string;
  category: string; priority: string; status: string; created_at: string; updated_at: string;
  ticket_id?: string;
}
interface MockMessage {
  message_id: string; session_id: string; role: 'user' | 'assistant'; content: string;
  result: Record<string, unknown> | null; request_id: string; created_at: string;
}
interface MockSession {
  session_id: string; user_id: string; title: string; created_at: string; updated_at: string;
  messages: MockMessage[]; drafts: MockDraft[];
}
interface MockTicket {
  ticket_id: string; user_id: string; draft_id: string | null; title: string; description: string;
  category: string; priority: string; status: string; created_at: string; updated_at: string;
}
export interface MockState {
  sessions: Record<string, MockSession>;
  tickets: MockTicket[];
  requests: { method: string; path: string; body: Record<string, unknown> | null }[];
  failMessageRequests: number;
  commitBeforeNetworkFailure: boolean;
  replyWithDraft: boolean;
  replyStatus: 'answered' | 'no_evidence' | 'failed';
  failConfirmRequests: number;
  confirmDelayMs: number;
  lostDraftUpdateResponses: number;
}

function makeSession(id = SESSION_ID): MockSession {
  return { session_id: id, user_id: 'demo-user', title: 'VPN 排查记录', created_at: NOW,
    updated_at: NOW, messages: [], drafts: [] };
}
export function makeDraft(sessionId = SESSION_ID): MockDraft {
  return { draft_id: DRAFT_ID, session_id: sessionId, user_id: 'demo-user', title: 'VPN 人工核查',
    description: 'VPN-403 设备未授权，按手册检查后仍失败，未自行修改白名单。', category: 'vpn',
    priority: 'normal', status: 'pending', created_at: NOW, updated_at: NOW };
}
export function answerResult(requestId = 'request-history-01'): Record<string, unknown> {
  return {
    run_id: 'run-react-mock-01', request_id: requestId, status: 'answered',
    classification: { category: 'vpn', intent: 'troubleshooting', priority: 'normal', missing_fields: [], needs_info: false },
    answer: '请先核对模拟设备登记状态；需要变更设备授权时交由人工核实。',
    citations: [{ chunk_id: 'vpn-001:000:mock', doc_id: 'vpn-001', title: 'VPN-403：设备登记检查',
      source: 'vpn/vpn-001.md', version: '1.0', content: '设备授权由人工核实。助手不能开通授权或修改白名单。' }],
    retrieved: [], draft: null, error: null,
    tool_trace: [{ name: 'search_knowledge', args: { query: 'VPN-403 设备未授权' }, status: 'ok', attempt: 1,
      result: { ok: true, data: { chunks: [] } }, elapsed_ms: 12 }],
    metrics: { retrieval_ms: 12, generation_ms: 24, total_ms: 36,
      input_tokens: 150, output_tokens: 50, embedding_tokens: 12, cost_usd: null },
  };
}
function appendTurn(session: MockSession, question: string, requestId: string, result: Record<string, unknown>) {
  if (session.messages.some(message => message.request_id === requestId && message.role === 'assistant')) return;
  session.messages.push({ message_id: `message-user-${requestId}`, session_id: session.session_id,
    role: 'user', content: question, result: null, request_id: requestId, created_at: NOW });
  session.messages.push({ message_id: `message-assistant-${requestId}`, session_id: session.session_id,
    role: 'assistant', content: String(result.answer), result, request_id: requestId, created_at: NOW });
}

export async function mockApi(page: Page, options: { history?: boolean; draft?: boolean } = {}): Promise<MockState> {
  const session = makeSession();
  if (options.history || options.draft) appendTurn(session, 'VPN-403 怎么处理？', 'request-history-01', answerResult());
  if (options.draft) session.drafts.push(makeDraft());
  const state: MockState = { sessions: { [session.session_id]: session }, tickets: [{
    ticket_id: 'IT-1001', user_id: 'demo-user', draft_id: null, title: 'VPN 设备登记检查',
    description: '演示工单：等待人工核对设备登记。', category: 'vpn', priority: 'normal', status: 'in_progress',
    created_at: NOW, updated_at: NOW,
  }], requests: [], failMessageRequests: 0, commitBeforeNetworkFailure: false, replyWithDraft: false,
  replyStatus: 'answered', failConfirmRequests: 0, confirmDelayMs: 0, lostDraftUpdateResponses: 0 };

  await page.route('**/api/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (!path.startsWith('/api/')) return route.fallback();
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : null;
    state.requests.push({ method, path, body });
    const json = (payload: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json',
      headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
        'Access-Control-Allow-Headers': 'content-type' }, body: JSON.stringify(payload) });
    const error = (status: number, code: string, message: string) => json({ detail: { code, message } }, status);
    if (method === 'OPTIONS') return json({});
    if (path === '/api/health') return json({
      status: 'ok', checks: { configuration: { status: 'ready', message: '本地配置字段齐全' }, index: { status: 'ready' } },
      index: { document_count: 24, chunk_count: 48, updated_at: NOW },
      models: { chat: 'mock-chat', embedding: 'mock-embedding' }, mode: 'local_demo_single_user',
      agent: { max_tool_attempts: 3, tools: 3, ticket_write: 'explicit_confirmation' },
    });
    if (path === '/api/sessions' && method === 'GET') {
      const rows = Object.values(state.sessions).map(({ session_id, user_id, title, created_at, updated_at }) =>
        ({ session_id, user_id, title, created_at, updated_at }));
      const offset = Number(url.searchParams.get('offset') || 0);
      const limit = Number(url.searchParams.get('limit') || 10);
      return json({ sessions: rows.slice(offset, offset + limit) });
    }
    if (path === '/api/sessions' && method === 'POST') {
      const id = `session-new-${Object.keys(state.sessions).length + 1}`;
      const created = { ...makeSession(id), title: '新会话' };
      state.sessions[id] = created;
      return json(created, 201);
    }
    const sessionMatch = path.match(/^\/api\/sessions\/([^/]+)$/);
    if (sessionMatch) {
      const current = state.sessions[sessionMatch[1]];
      if (!current) return error(404, 'session_not_found', '会话不存在。');
      if (method === 'GET') return json(current);
      if (method === 'PATCH') { current.title = String(body?.title || '').trim(); return json(current); }
      if (method === 'DELETE') {
        for (const ticket of state.tickets) if (current.drafts.some(draft => draft.draft_id === ticket.draft_id)) ticket.draft_id = null;
        delete state.sessions[current.session_id];
        return json({ status: 'session_deleted', session_id: current.session_id });
      }
    }
    const messageMatch = path.match(/^\/api\/sessions\/([^/]+)\/messages$/);
    if (messageMatch && method === 'POST') {
      const current = state.sessions[messageMatch[1]];
      if (!current) return error(404, 'session_not_found', '会话不存在。');
      const question = String(body?.question || body?.text || '');
      const requestId = String(body?.request_id || '');
      const prior = current.messages.find(message => message.request_id === requestId && message.role === 'assistant');
      if (prior) return json(prior.result);
      const result = answerResult(requestId);
      if (state.replyWithDraft) {
        const draft = makeDraft(current.session_id);
        current.drafts.push(draft);
        result.status = 'pending_confirmation'; result.draft = draft;
        result.answer = '已准备工单草稿，请核对内容并点击确认创建。';
      } else if (state.replyStatus !== 'answered') {
        result.status = state.replyStatus; result.citations = [];
        result.answer = state.replyStatus === 'failed' ? '本轮未能完成，请稍后重试。' : '当前资料不足，请补充具体错误码或联系人工。';
        result.error = state.replyStatus === 'failed' ? { code: 'provider_timeout', message: '模型请求超时，请稍后重试。' } : null;
      }
      if (state.failMessageRequests > 0) {
        state.failMessageRequests -= 1;
        if (state.commitBeforeNetworkFailure) appendTurn(current, question, requestId, result);
        return route.abort('failed');
      }
      appendTurn(current, question, requestId, result);
      return json(result);
    }
    const draftMatch = path.match(/^\/api\/drafts\/([^/]+)(?:\/(confirm|cancel))?$/);
    if (draftMatch) {
      const draft = Object.values(state.sessions).flatMap(current => current.drafts).find(item => item.draft_id === draftMatch[1]);
      if (!draft) return error(404, 'draft_not_found', '草稿不存在。');
      if (method === 'GET') return json(draft);
      if (method === 'PATCH') {
        if (draft.status !== 'pending') return error(409, 'draft_not_pending', '仅待确认草稿可以修改。');
        Object.assign(draft, { title: body?.title, description: body?.description, category: body?.category });
        if (state.lostDraftUpdateResponses > 0) { state.lostDraftUpdateResponses -= 1; return route.abort('failed'); }
        return json({ status: 'draft_updated', draft });
      }
      if (method === 'POST' && draftMatch[2] === 'cancel') {
        if (draft.status === 'confirmed') return error(409, 'draft_confirmed', '已创建工单，不能取消。');
        draft.status = 'cancelled'; return json({ status: 'cancelled', draft });
      }
      if (method === 'POST' && draftMatch[2] === 'confirm') {
        if (state.confirmDelayMs) await new Promise(resolve => setTimeout(resolve, state.confirmDelayMs));
        if (state.failConfirmRequests > 0) { state.failConfirmRequests -= 1; return error(503, 'storage_unavailable', '本地存储不可用，请重试。'); }
        if (draft.status === 'cancelled') return error(409, 'draft_cancelled', '草稿已取消，不能提交。');
        let ticket = state.tickets.find(item => item.draft_id === draft.draft_id);
        if (!ticket) {
          ticket = { ticket_id: 'IT-REACT0001', user_id: 'demo-user', draft_id: draft.draft_id,
            title: draft.title, description: draft.description, category: draft.category,
            priority: 'normal', status: 'open', created_at: NOW, updated_at: NOW };
          state.tickets.push(ticket);
        }
        draft.status = 'confirmed'; draft.ticket_id = ticket.ticket_id;
        return json({ status: 'ticket_created', ticket, draft });
      }
    }
    if (path === '/api/tickets' && method === 'GET') {
      const offset = Number(url.searchParams.get('offset') || 0);
      const limit = Number(url.searchParams.get('limit') || 30);
      return json({ tickets: state.tickets.slice(offset, offset + limit) });
    }
    if (path.startsWith('/api/tickets/') && method === 'GET') {
      const ticket = state.tickets.find(item => item.ticket_id === path.split('/').at(-1));
      return ticket ? json(ticket) : error(404, 'ticket_not_found', '工单不存在或不可查看。');
    }
    return error(404, 'mock_route_missing', `No mock for ${method} ${path}`);
  });
  return state;
}
