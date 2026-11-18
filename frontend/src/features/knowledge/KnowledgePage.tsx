import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { BookOpen, Database, FileText, RefreshCw, Search, SlidersHorizontal } from 'lucide-react';
import { ApiError, errorText } from '../../api/errors';
import { DocumentsPanel } from './DocumentsPanel';
import { ProfilePanel } from './ProfilePanel';
import { RecallPanel } from './RecallPanel';
import { knowledgeApi, strategyLabels, type Catalog, type ImportFile, type Profile, type Workspace } from './workspaceApi';
import './knowledge.css';

const tabs = [
  { id: 'documents', label: '文档', icon: FileText },
  { id: 'recall', label: '召回测试', icon: Search },
  { id: 'settings', label: '设置', icon: SlidersHorizontal },
] as const;
const statusNames: Record<string, string> = { ready: '索引可用', empty: '未构建', stale: '待重建', invalid: '索引异常' };
const uncertainCodes = ['request_timeout', 'network_error', 'invalid_response'];

export function KnowledgePage({ onPublished }: { onPublished: () => Promise<void> }) {
  const [data, setData] = useState<Workspace>();
  const [catalog, setCatalog] = useState<Catalog>();
  const [tab, setTab] = useState<typeof tabs[number]['id']>('documents');
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [verificationRequired, setVerificationRequired] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  const latestRead = useRef(0);
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);

  const refresh = useCallback(async () => {
    const current = ++latestRead.current;
    setLoading(true);
    setError('');
    const [workspaceResult, catalogResult] = await Promise.allSettled([knowledgeApi.getWorkspace(), knowledgeApi.catalog()]);
    if (current !== latestRead.current) return;
    setLoading(false);
    if (catalogResult.status === 'fulfilled') setCatalog(catalogResult.value);
    else setError(errorText(catalogResult.reason));
    if (workspaceResult.status === 'fulfilled') {
      setData(workspaceResult.value);
      setVerificationRequired(false);
    } else {
      setError(errorText(workspaceResult.reason));
      throw workspaceResult.reason;
    }
  }, []);

  useEffect(() => {
    void refresh().catch(() => {});
    return () => { latestRead.current += 1; };
  }, [refresh]);

  async function apply(profile: Profile, files: ImportFile[] = [], removeIds: string[] = []): Promise<Workspace> {
    if (!data || inFlight.current || loading || verificationRequired) {
      throw new ApiError('workspace_busy', '请等待当前操作完成，或刷新后再试。');
    }
    inFlight.current = true;
    setApplying(true);
    setNotice('');
    try {
      const updated = await knowledgeApi.apply(profile, data.revision, files, removeIds);
      setData(updated);
      if (files.length) setNotice(`已导入 ${files.length} 篇文档并更新索引。`);
      else if (removeIds.length) setNotice('文档已移除，索引已更新。');
      else setNotice(updated.rebuilt ? '索引已建立，新设置已生效。' : '新设置已生效。');
      await Promise.allSettled([refresh(), onPublished()]);
      return updated;
    } catch (failure) {
      if (failure instanceof ApiError && ['workspace_conflict', ...uncertainCodes].includes(failure.code)) {
        setVerificationRequired(true);
        await Promise.allSettled([refresh(), onPublished()]);
      }
      throw failure;
    } finally {
      inFlight.current = false;
      setApplying(false);
    }
  }

  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    let next: number;
    switch (event.key) {
      case 'ArrowLeft': next = (index + tabs.length - 1) % tabs.length; break;
      case 'ArrowRight': next = (index + 1) % tabs.length; break;
      case 'Home': next = 0; break;
      case 'End': next = tabs.length - 1; break;
      default: return;
    }
    event.preventDefault();
    setTab(tabs[next].id);
    tabButtons.current[next]?.focus();
  }

  const busy = loading || applying || verificationRequired;
  const profile = data?.profile;
  const documentCount = catalog?.documents.length;
  const chunkCount = catalog?.documents.reduce((sum, document) => sum + document.chunk_count, 0);

  return <section className="knowledge-page" aria-labelledby="knowledge-title">
    <header className="kw-hero">
      <div className="kw-hero-heading">
        <div className="kw-hero-icon"><BookOpen size={25} /></div>
        <div><h1 id="knowledge-title">知识库</h1><p className="muted">管理 IT 手册，验证问答使用的片段。</p></div>
        <button type="button" className="button secondary kw-refresh" disabled={loading || applying}
          onClick={() => void refresh().catch(() => {})}><RefreshCw size={15} />刷新<span className="screen-reader">知识库</span></button>
      </div>
      <div className="kw-overview">
        <span className={`kw-index${data?.index.status === 'ready' ? ' is-ready' : ''}`} title={data?.index.message}>
          <span />{data ? statusNames[data.index.status] ?? data.index.status : '索引状态未知'}</span>
        <span>{documentCount ?? '—'} 篇文档 · {chunkCount ?? '—'} 个片段</span>
        {profile && <span><Database size={14} />{profile.embedding_model || 'Embedding 未配置'}</span>}
        {profile && <span>{strategyLabels[profile.chunk_strategy]} · {profile.chunk_size} / {profile.chunk_overlap} 字符</span>}
      </div>
    </header>

    <div className="kw-tabs" role="tablist" aria-label="知识库功能" aria-orientation="horizontal">
      {tabs.map(({ id, label, icon: Icon }, index) => <button key={id} type="button" role="tab" id={`kw-tab-${id}`}
        ref={element => { tabButtons.current[index] = element; }} aria-selected={tab === id} aria-controls={`kw-panel-${id}`}
        tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)} onKeyDown={event => navigateTabs(event, index)}>
        <Icon size={16} />{label}
      </button>)}
    </div>

    {loading && <p className="kw-message muted" role="status">正在读取知识库…</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    {verificationRequired && <p className="error" role="status">上次操作结果待确认。请先刷新知识库版本。</p>}

    <div role="tabpanel" id="kw-panel-documents" aria-labelledby="kw-tab-documents" hidden={tab !== 'documents'}>
      {tab === 'documents' && <DocumentsPanel catalog={catalog} workspace={data} busy={busy} onRefresh={refresh}
        onApply={async (files, removeIds) => {
          if (!profile) throw new ApiError('workspace_unavailable', '请先刷新知识库设置。');
          await apply(profile, files, removeIds);
        }} />}
    </div>
    <div role="tabpanel" id="kw-panel-recall" aria-labelledby="kw-tab-recall" hidden={tab !== 'recall'}>
      {tab === 'recall' && <RecallPanel key={data?.revision} workspace={data} busy={busy} />}
    </div>
    <div role="tabpanel" id="kw-panel-settings" aria-labelledby="kw-tab-settings" hidden={tab !== 'settings'}>
      {tab === 'settings' && (data ? <ProfilePanel workspace={data} busy={busy} onApply={apply} />
        : <p className="kw-card muted">正在等待可用的知识库配置。</p>)}
    </div>
  </section>;
}
