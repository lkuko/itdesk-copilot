import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { BookOpen, ChevronLeft, ChevronRight, Headphones, MessageSquare, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, Settings, ShieldCheck, Ticket, X } from 'lucide-react';
import { api } from '../api/client';
import { ApiError, errorText } from '../api/errors';
import { sensitiveInputReason } from '../api/safety';
import type { Health, SessionDetail, SessionSummary } from '../api/types';
import ChatView from '../features/chat/ChatView';
import { formatTime } from '../features/chat/formatTime';
import { clearPending, loadPending, savePending } from '../features/chat/pending';
import type { PendingRequest } from '../features/chat/pending';
import SettingsPage from '../features/settings/SettingsPage';
import { loadPreferences } from '../features/settings/preferences';
import type { Preferences } from '../features/settings/preferences';
import SessionDialog from '../features/sessions/SessionDialog';
import { TicketsPage } from '../features/tickets/TicketsPage';
import { conversationPath, ROUTES } from './routes';

type Phase = 'idle' | 'submitting' | 'success' | 'timeout' | 'retryable_error' | 'abandoned';
const PAGE_SIZE = 10;
const KnowledgePage = lazy(() => import('../features/knowledge/KnowledgePage').then(module => ({ default: module.KnowledgePage })));
export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [preferences, setPreferences] = useState<Preferences>(loadPreferences);
  const [pending, setPending] = useState<PendingRequest | null>(loadPending);
  const [activeSession, setActiveSession] = useState(() => new URLSearchParams(window.location.search).get('session') || loadPending()?.session_id || '');
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState('');
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [offset, setOffset] = useState(0);
  const [sessionsError, setSessionsError] = useState('');
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [question, setQuestion] = useState('');
  const [phase, setPhase] = useState<Phase>(() => loadPending() ? 'retryable_error' : 'idle');
  const [requestErrorCode, setRequestErrorCode] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(() => window.matchMedia('(min-width: 900px)').matches);
  const [managing, setManaging] = useState<SessionSummary | null>(null);
  const [creating, setCreating] = useState(false);
  const submitLock = useRef(false);
  const creationLock = useRef(false);
  const activeRef = useRef(activeSession);
  const pendingRef = useRef(pending);
  const offsetRef = useRef(offset);
  const detailVersion = useRef(0);
  const sessionsVersion = useRef(0);
  activeRef.current = activeSession;
  pendingRef.current = pending;
  offsetRef.current = offset;
  const submitting = phase === 'submitting';
  const ready = health?.status === 'ok' && !healthError;

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = preferences.theme;
    root.dataset.fontSize = preferences.font_size;
    root.dataset.density = preferences.density;
  }, [preferences]);
  useEffect(() => {
    if (location.pathname === ROUTES.chat) {
      const id = new URLSearchParams(location.search).get('session') || '';
      if (!id && pendingRef.current) { navigate(conversationPath(pendingRef.current.session_id), { replace: true }); return; }
      setActiveSession(id);
    }
  }, [location.pathname, location.search, navigate]);
  const refreshHealth = useCallback(async () => {
    try { setHealth(await api.health()); setHealthError(''); }
    catch (failure) { setHealth(null); setHealthError(errorText(failure)); }
  }, []);
  const refreshSessions = useCallback(async () => {
    if (offsetRef.current !== offset) return;
    const version = ++sessionsVersion.current;
    try {
      const result = await api.listSessions(offset, PAGE_SIZE);
      if (offsetRef.current === offset && sessionsVersion.current === version) { setSessions(result.sessions); setSessionsError(''); }
    } catch (failure) { if (offsetRef.current === offset && sessionsVersion.current === version) setSessionsError(errorText(failure)); }
  }, [offset]);
  const refreshSession = useCallback(async (id: string) => {
    // A completed send in another session must not invalidate the visible session's GET.
    const version = activeRef.current === id ? ++detailVersion.current : null;
    if (!id) { if (version !== null) { setDetail(null); setLoadingDetail(false); } return; }
    if (version !== null) setLoadingDetail(true);
    try {
      const result = await api.getSession(id);
      if (version !== null && activeRef.current === id && version === detailVersion.current) { setDetail(result); setError(''); }
      const currentPending = pendingRef.current;
      const matchesQuestion = currentPending && result.messages.some(message => message.role === 'user' && message.request_id === currentPending.request_id && message.content === currentPending.question);
      if (currentPending?.session_id === id && matchesQuestion && result.messages.some(message => message.role === 'assistant' && message.result?.request_id === currentPending.request_id)) {
        clearPending(); setPending(null); pendingRef.current = null; setPhase('success'); setRequestErrorCode(''); setNotice('原请求已完成，已恢复保存的结果。');
      }
    } catch (failure) { if (version !== null && activeRef.current === id && version === detailVersion.current) { setError(errorText(failure)); setDetail(null); } }
    finally { if (version !== null && activeRef.current === id && version === detailVersion.current) setLoadingDetail(false); }
  }, []);
  const refreshActive = useCallback(async () => { await refreshSession(activeRef.current); }, [refreshSession]);
  useEffect(() => { void refreshHealth(); }, [refreshHealth]);
  useEffect(() => { void refreshSessions(); }, [refreshSessions]);
  useEffect(() => { setDetail(null); void refreshSession(activeSession); }, [activeSession, refreshSession]);

  function selectSession(id: string) {
    setActiveSession(id); activeRef.current = id; setError(''); setNotice('');
    navigate(conversationPath(id));
    if (window.matchMedia('(max-width: 899px)').matches) setSidebarOpen(false);
  }
  async function createSession() {
    if (submitting || creationLock.current) return;
    creationLock.current = true;
    setCreating(true); setError('');
    try { const session = await api.createSession(); setOffset(0); selectSession(session.session_id); setNotice('新会话已就绪。'); await refreshSessions(); }
    catch (failure) { setError(errorText(failure)); } finally { creationLock.current = false; setCreating(false); }
  }
  async function execute(request: PendingRequest) {
    if (submitLock.current) return;
    submitLock.current = true; setPhase('submitting'); setError(''); setNotice(''); setRequestErrorCode('');
    setPending(request); pendingRef.current = request;
    if (!savePending(request)) setNotice('当前浏览器不允许暂存请求。请等待结果，刷新前先确认是否完成。');
    try {
      await api.sendMessage(request.session_id, request.question, request.request_id);
      clearPending(); setPending(null); pendingRef.current = null; setPhase('success'); setQuestion('');
      await Promise.all([refreshSession(request.session_id), refreshSessions()]);
    } catch (failure) {
      const code = failure instanceof ApiError ? failure.code : 'unknown_error';
      setRequestErrorCode(code); setError(errorText(failure));
      if (failure instanceof ApiError && failure.status === 422) {
        clearPending(); setPending(null); pendingRef.current = null; setPhase('idle');
      } else { setPhase(code === 'request_timeout' ? 'timeout' : 'retryable_error'); }
    } finally { submitLock.current = false; }
  }
  async function submit() {
    const text = question.trim();
    if (!text || !ready || pendingRef.current || submitLock.current) return;
    const reason = sensitiveInputReason(text);
    if (reason) { setError(reason); return; }
    let id = activeRef.current;
    if (!id) {
      if (creationLock.current) return;
      creationLock.current = true;
      setCreating(true);
      try { const session = await api.createSession(); id = session.session_id; selectSession(id); }
      catch (failure) { setError(errorText(failure)); return; } finally { creationLock.current = false; setCreating(false); }
    }
    await execute({ session_id: id, request_id: crypto.randomUUID(), question: text, started_at: new Date().toISOString() });
  }
  async function retry(newId = false) {
    if (!pendingRef.current || submitLock.current) return;
    const request = pendingRef.current;
    selectSession(request.session_id);
    // A persisted user message without a result cannot be replayed. New ID is explicit user intent.
    await execute(newId ? { ...request, request_id: crypto.randomUUID(), started_at: new Date().toISOString() } : request);
  }
  function abandon() { clearPending(); setPending(null); pendingRef.current = null; setPhase('abandoned'); setError(''); setRequestErrorCode(''); setNotice('已放弃前端重试；后端原请求可能仍会完成，可刷新会话查看。'); }
  async function refreshAll() { await Promise.all([refreshHealth(), refreshSessions(), refreshActive()]); }
  async function refreshPending() {
    const request = pendingRef.current;
    if (!request) return;
    selectSession(request.session_id);
    await refreshSession(request.session_id);
  }
  async function changedSession(deleted: boolean) {
    if (deleted && managing?.session_id === pendingRef.current?.session_id) { clearPending(); setPending(null); pendingRef.current = null; setPhase('abandoned'); setRequestErrorCode(''); }
    if (deleted && managing?.session_id === activeSession) selectSession('');
    await refreshSessions(); if (!deleted) await refreshActive();
  }
  const chatPath = conversationPath(activeSession);
  const navigateTo = () => { if (window.matchMedia('(max-width: 899px)').matches) setSidebarOpen(false); setError(''); setNotice(''); };
  return <div className={`app-shell ${sidebarOpen ? 'sidebar-open' : 'sidebar-closed'}`}>
    <a className="skip-link" href="#main-content">跳转到主内容</a>
    {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭导航遮罩" onClick={() => setSidebarOpen(false)} />}
    <aside className="sidebar" aria-label="主导航" aria-hidden={!sidebarOpen} inert={!sidebarOpen}>
      <div className="brand-row"><Link className="brand" to={chatPath} onClick={navigateTo}><span className="brand-mark"><Headphones size={23} /></span><span><strong>ITDesk Copilot</strong><small>你的 IT 服务工作台</small></span></Link><button className="icon-button sidebar-close" aria-label="收起侧边栏" onClick={() => setSidebarOpen(false)}><PanelLeftClose size={18} /></button></div>
      <span className="local-label"><span />本地模拟</span>
      <button className="button primary new-conversation" disabled={submitting || creating || !health} onClick={() => void createSession()}><Plus size={18} />{creating ? '正在新建……' : '新会话'}</button>
      <nav className="main-navigation"><Link to={chatPath} onClick={navigateTo} className={location.pathname === '/chat' || location.pathname === '/' ? 'selected' : ''}><MessageSquare size={17} />问答助手</Link><Link to={ROUTES.tickets} onClick={navigateTo} className={location.pathname === '/tickets' ? 'selected' : ''}><Ticket size={17} />历史工单</Link>
        <Link to={ROUTES.knowledge} onClick={navigateTo} className={location.pathname === ROUTES.knowledge ? 'selected' : ''} aria-current={location.pathname === ROUTES.knowledge ? 'page' : undefined}><BookOpen size={17} />知识库</Link>
      </nav>
      <div className="history-heading"><span>最近会话</span><button className="icon-button" aria-label="刷新会话" onClick={() => void refreshSessions()}><RefreshCw size={13} /></button></div>
      {sessionsError && <p className="error small" role="alert">{sessionsError}</p>}
      <div className="session-history">{!sessions.length && !sessionsError && <p className="muted small">暂无历史会话</p>}{sessions.map(session => <div className={`session-row ${session.session_id === activeSession && location.pathname === '/chat' ? 'active' : ''}`} key={session.session_id}><button className="session-select" onClick={() => selectSession(session.session_id)} disabled={submitting}><span>{session.title || '新会话'}</span><time>{formatTime(session.updated_at)}</time></button><button className="icon-button session-menu" aria-label={`管理会话 ${session.title || '新会话'}`} title="管理会话" disabled={submitting} onClick={() => setManaging(session)}><MoreHorizontal size={16} /></button></div>)}</div>
      {(offset > 0 || sessions.length === PAGE_SIZE) && <div className="history-pagination"><button aria-label="较新会话" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}><ChevronLeft size={14} /></button><span>{offset + 1}–{offset + sessions.length}</span><button aria-label="较早会话" disabled={sessions.length < PAGE_SIZE} onClick={() => setOffset(value => value + PAGE_SIZE)}><ChevronRight size={14} /></button></div>}
      <details className="sidebar-service"><summary>知识与服务</summary><p className="small muted">{health?.index?.document_count ?? '—'} 篇手册 · {health?.index?.chunk_count ?? '—'} 个片段</p><p className="small muted">{health?.status === 'ok' ? '本地配置与索引就绪' : '服务待检查'}</p><button className="text-button" onClick={() => void refreshAll()}>刷新会话与状态</button></details>
      <footer className="sidebar-footer"><Link to={ROUTES.settings} onClick={navigateTo} className={location.pathname === '/settings' ? 'selected' : ''}><Settings size={17} />设置</Link><p>本机演示空间 · 会话保存在本地</p></footer>
    </aside>
    <div className="workspace"><header className="topbar"><div className="topbar-left">{!sidebarOpen && <button className="icon-button" aria-label="展开侧边栏" onClick={() => setSidebarOpen(true)}><PanelLeftOpen size={19} /></button>}<Link to={chatPath} className="workspace-title">ITDesk <span>/ IT 服务助手</span></Link></div><span className={`service-badge ${ready ? 'ready' : 'not-ready'}`}><span />{ready ? '服务已就绪' : '服务待检查'}</span></header><main id="main-content" tabIndex={-1} className="main-content"><div className="global-feedback">{notice && <div className="notice" role="status"><ShieldCheck size={17} />{notice}<button className="icon-button" aria-label="关闭提示" onClick={() => setNotice('')}><X size={14} /></button></div>}{(error || healthError) && <div className="error" role="alert">{error || healthError}<button className="text-button" onClick={() => void refreshAll()}>刷新会话与状态</button></div>}</div><Routes>
      <Route path="/" element={<Navigate to={pending ? conversationPath(pending.session_id) : ROUTES.chat} replace />} />
      <Route path={ROUTES.chat} element={<ChatView detail={detail} loading={loadingDetail} preferences={preferences} ready={ready} question={question} onQuestion={setQuestion} onSubmit={() => void submit()} isSubmitting={submitting || creating} pending={pending} phase={phase} onRetry={newId => void retry(newId)} onRefresh={refreshActive} onRefreshPending={refreshPending} onRefreshHealth={refreshHealth} onAbandon={abandon} requestErrorCode={requestErrorCode} onNotice={setNotice} onError={setError} />} />
      <Route path={ROUTES.tickets} element={<TicketsPage />} />
      <Route path={ROUTES.knowledge} element={<Suspense fallback={<p role="status">正在加载知识库…</p>}>
        <KnowledgePage onPublished={refreshHealth} />
      </Suspense>} />
      <Route path={ROUTES.settings} element={<SettingsPage preferences={preferences} onApply={setPreferences} health={health} />} />
      <Route path="*" element={<Navigate to={ROUTES.chat} replace />} />
    </Routes></main></div>
    {managing && <SessionDialog key={managing.session_id} session={managing} onClose={() => setManaging(null)} onChanged={changedSession} onNotice={setNotice} />}
  </div>;
}
