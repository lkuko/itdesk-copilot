import { useState, type FormEvent } from 'react';
import { fetchJson } from '../../api/client';
import { ApiError, errorText } from '../../api/errors';
import { sensitiveInputReason } from '../../api/safety';
import { ChunkText } from './DocumentContent';
import type { Chunk, DocumentSummary } from './workspaceApi';

type StrategyResult = {
  strategy: 'boundary' | 'fixed';
  chunk_count: number;
  embedding_characters: number;
  hits: (Chunk & DocumentSummary & { similarity: number })[];
};
type Comparison = {
  query: string;
  top_k: number;
  embedding_model: string;
  chunk_size: number;
  chunk_overlap: number;
  document_embedding_tokens: number | null;
  query_embedding_tokens: number | null;
  unique_chunk_inputs: number;
  strategies: StrategyResult[];
};
const strategyNames = { boundary: '优先自然边界', fixed: '固定字符窗口' };
const formatNumber = (count: number | null) => count == null ? '未知' : count.toLocaleString('zh-CN');

export function StrategyExperiment() {
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const [result, setResult] = useState<Comparison>();
  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState('');

  async function compare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (comparing || !query.trim()) return;
    const blocked = sensitiveInputReason(query);
    if (blocked) {
      setCompareError(blocked);
      return;
    }
    setComparing(true);
    setCompareError('');
    setResult(undefined);
    try {
      setResult(await fetchJson<Comparison>('/api/knowledge/compare', {
        method: 'POST', body: { query: query.trim(), top_k: topK },
      }));
    } catch (error) {
      setCompareError(error instanceof ApiError && error.code === 'request_timeout'
        ? '对比超时，请稍后手动重试。' : errorText(error));
    } finally {
      setComparing(false);
    }
  }

  return <section className="lab-compare" aria-labelledby="lab-compare">
        <h2 id="lab-compare">切片策略对比</h2>
        <p className="muted">两组使用相同手册、窗口、重叠和 Embedding 模型。每次对比会调用该模型，不修改问答索引。</p>
        <form className="lab-form" onSubmit={event => void compare(event)}>
          <label>测试查询<input value={query} placeholder="例如：VPN 反复验证失败" maxLength={500} required
            disabled={comparing} onChange={event => setQuery(event.target.value)} /></label>
          <label>Top K<select value={topK} disabled={comparing} onChange={event => setTopK(Number(event.target.value))}>
            {[1, 3, 5, 10].map(k => <option key={k}>{k}</option>)}
          </select></label>
          <button className="button primary" disabled={comparing || !query.trim()}>{comparing ? '对比中…' : '运行对比'}</button>
        </form>
        {comparing && <p role="status">正在生成向量与计算相似度…</p>}
        {compareError && <p className="error" role="alert">{compareError}</p>}
        {result && <>
          <p role="status" className="lab-result-header">“{result.query}” · Top {result.top_k} · {result.embedding_model}</p>
          <p className="muted lab-result-meta">去重后 {result.unique_chunk_inputs} 个切片输入 · 文档 {formatNumber(result.document_embedding_tokens)} Token · 查询 {formatNumber(result.query_embedding_tokens)} Token</p>
          <p className="muted lab-result-meta">余弦相似度介于 −1 和 1，越高越相近。分数供检索比较，不代表答复正确率。</p>
          <div className="lab-strategies">
            {result.strategies.map(group => <section key={group.strategy}>
              <h3>{strategyNames[group.strategy]}</h3>
              <p className="muted">{group.chunk_count} 片 · {formatNumber(group.embedding_characters)} 输入字符</p>
              {!group.hits.length && <p className="muted">暂无返回片段。</p>}
              <ol className="lab-hits">
                {group.hits.map((hit, rank) => <li key={hit.chunk_id}>
                  <div className="lab-hit-heading"><strong>{rank + 1}. {hit.title}</strong><span className="lab-score">{hit.similarity.toFixed(3)}</span></div>
                  <meter aria-label="余弦相似度" min={-1} max={1} value={hit.similarity} />
                  <p className="muted lab-path">{hit.source}{hit.version ? ` · ${hit.version}` : ''}</p>
                  <details><summary>核对片段内容</summary><ChunkText chunk={hit} /></details>
                </li>)}
              </ol>
            </section>)}
          </div>
        </>}
      </section>;
}
