import { useState, type FormEvent } from 'react';
import { FlaskConical, Search } from 'lucide-react';
import { errorText } from '../../api/errors';
import { sensitiveInputReason } from '../../api/safety';
import { StrategyExperiment } from './StrategyExperiment';
import { knowledgeApi, searchLabels, type SearchResponse, type Workspace } from './workspaceApi';

const rerankLabels = {
  disabled: '未启用重排',
  applied: '已重排',
  fallback: '重排不可用，已保留原排序',
  no_candidates: '暂无待重排的候选',
};

export function RecallPanel({ workspace, busy }: { workspace?: Workspace; busy: boolean }) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<{ query: string; data: SearchResponse }>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [experiment, openExperiment] = useState(false);
  const available = workspace?.index.status === 'ready' && !busy;
  const profile = workspace?.profile;

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = query.trim();
    if (!text || !available || loading) return;
    const blocked = sensitiveInputReason(text);
    if (blocked) { setError(blocked); return; }
    setError('');
    setResult(undefined);
    setLoading(true);
    try { setResult({ query: text, data: await knowledgeApi.search(text) }); }
    catch (failure) { setError(errorText(failure)); }
    finally { setLoading(false); }
  }

  return <>
    <section className="kw-card" aria-labelledby="kw-recall">
      <div className="kw-card-heading"><div><h2 id="kw-recall">召回测试</h2>
        <p className="muted">输入一条查询，查看当前索引返回的知识片段。</p></div></div>
      {profile && <p className="kw-recall-profile muted">{searchLabels[profile.search_mode]} · Top {profile.top_k} · 相似度下限 {profile.score_threshold.toFixed(2)}
        {profile.rerank_model && ` · ${profile.rerank_model === 'local-lexical' ? '本地词法重排' : profile.rerank_model}`}</p>}
      {workspace && workspace.index.status !== 'ready' && <p className="error" role="status">索引{workspace.index.status === 'empty' ? '尚未建立' : '当前不可用'}。请在设置中确认可用模型，并应用索引设置。</p>}
      <form className="kw-recall-form" onSubmit={search}>
        <label className="kw-field">测试查询<input value={query} maxLength={500} required disabled={loading}
          onChange={event => setQuery(event.target.value)} placeholder="例如：VPN 反复验证失败" /></label>
        <button type="submit" className="button primary" disabled={!available || !query.trim() || loading}><Search size={16} />运行召回</button>
      </form>
      {loading && <p role="status">正在检索知识片段…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {result && <div className="kw-recall-results">
        <p role="status"><strong>“{result.query}”</strong> · {result.data.hits.length} 条结果 · {rerankLabels[result.data.rerank_status]}</p>
        <p className="muted small">查询 Embedding 用量 {result.data.embedding_tokens ?? '未知'} Token</p>
        {result.data.hits.length === 0 && <p className="kw-empty muted">没有片段符合当前条件。可以用具体错误码或故障描述再试一次。</p>}
        <ol>
          {result.data.hits.map((hit, index) => <li className="kw-hit" key={hit.chunk_id}>
            <header><strong>{index + 1}. {hit.title}</strong>
              {hit.exact_match && <span className="kw-origin">精确匹配</span>}</header>
            <p className="muted small">{hit.source} · {hit.version} · {hit.doc_id}</p>
            <p className="kw-scores">
              <span>余弦相似度 <strong>{hit.similarity == null ? '未计分' : hit.similarity.toFixed(3)}</strong></span>
              {hit.rerank_score != null && <span>重排分数 <strong>{hit.rerank_score.toFixed(3)}</strong></span>}
            </p>
            <pre className="lab-text">{hit.content}</pre>
          </li>)}
        </ol>
      </div>}
    </section>
    <details className="kw-card kw-experiment" onToggle={event => { if (event.currentTarget.open) openExperiment(true); }}>
      <summary><FlaskConical size={17} />两种切片策略对比</summary>
      {experiment && <StrategyExperiment />}
    </details>
  </>;
}
