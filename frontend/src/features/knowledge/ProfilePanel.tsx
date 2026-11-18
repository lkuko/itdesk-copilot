import { useState, type FormEvent } from 'react';
import { Save, SlidersHorizontal } from 'lucide-react';
import { errorText } from '../../api/errors';
import { KnowledgeDialog } from './KnowledgeDialog';
import { searchLabels, strategyLabels, type Profile, type Workspace } from './workspaceApi';

function wholeInRange(value: number, min: number, max: number) {
  return Number.isInteger(value) && value >= min && value <= max;
}

function validate(profile: Profile, workspace: Workspace) {
  if (!workspace.embedding_models.includes(profile.embedding_model)) return '请从后端已配置的 Embedding 模型中选择。';
  if (!wholeInRange(profile.chunk_size, 100, 2000)) return '分段长度必须是 100 到 2,000 之间的整数。';
  if (!wholeInRange(profile.chunk_overlap, 0, Math.min(99, profile.chunk_size - 1))) return '重叠长度必须是 0 到 99 之间的整数，且小于分段长度。';
  if (!wholeInRange(profile.top_k, 1, 10)) return 'Top K 必须是 1 到 10 之间的整数。';
  if (!Number.isFinite(profile.score_threshold) || profile.score_threshold < 0 || profile.score_threshold > 1) return '相似度下限必须在 0 和 1 之间。';
  if (!wholeInRange(profile.rerank_top_n, profile.rerank_model ? profile.top_k : 1, 30)) return '重排候选数必须在 1 到 30 之间，启用时至少等于 Top K。';
  return '';
}

type NumberKey = 'chunk_size' | 'chunk_overlap' | 'top_k' | 'score_threshold' | 'rerank_top_n';

export function ProfilePanel({ workspace, busy, onApply }: {
  workspace: Workspace; busy: boolean; onApply: (profile: Profile) => Promise<Workspace>;
}) {
  const [draft, setDraft] = useState(workspace.profile);
  const [revision, setRevision] = useState(workspace.revision);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const current = revision === workspace.revision;
  const changed = JSON.stringify(draft) !== JSON.stringify(workspace.profile);
  const indexNeedsBuild = workspace.index.status !== 'ready';
  const rebuild = indexNeedsBuild || ['embedding_model', 'chunk_strategy', 'chunk_size', 'chunk_overlap'].some(
    key => draft[key as keyof Profile] !== workspace.profile[key as keyof Profile],
  );

  function change<K extends keyof Profile>(key: K, value: Profile[K]) {
    setDraft(profile => ({ ...profile, [key]: value }));
    setError('');
  }

  function loadPublished() {
    setDraft(workspace.profile);
    setRevision(workspace.revision);
    setError('');
  }

  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!current || busy || (!changed && !indexNeedsBuild)) return;
    const problem = validate(draft, workspace);
    if (problem) { setError(problem); return; }
    setConfirming(true);
  }

  async function save() {
    if (!current || busy || saving) return;
    setSaving(true);
    setError('');
    try {
      const published = await onApply(draft);
      setDraft(published.profile);
      setRevision(published.revision);
      setConfirming(false);
    }
    catch (failure) { setError(errorText(failure)); }
    finally { setSaving(false); }
  }

  function numeric(key: NumberKey, label: string, min: number, max: number, step: number | 'any' = 1) {
    const value = draft[key];
    return <label className="kw-field">{label}<input type="number" min={min} max={max} step={step} required
      value={Number.isNaN(value) ? '' : value} onChange={event => change(key, event.target.valueAsNumber)} /></label>;
  }

  return <section className="kw-card" aria-labelledby="kw-profile">
    <div className="kw-card-heading">
      <div><h2 id="kw-profile">知识库设置</h2><p className="muted">应用后的配置用于聊天问答和召回测试。</p></div>
      <span className="kw-config-icon"><SlidersHorizontal size={19} /></span>
    </div>
    {!current && <p className="error" role="alert">知识库已更新。请载入当前设置后重新编辑。</p>}
    {error && !confirming && <p className="error" role="alert">{error}</p>}
    <form onSubmit={review}>
      <fieldset className="kw-settings-group" disabled={busy || !current}>
        <legend>向量与分段</legend>
        <div className="kw-fields">
          <label className="kw-field">Embedding 模型
            <select value={draft.embedding_model} onChange={event => change('embedding_model', event.target.value)}>
              {!draft.embedding_model && <option value="">未配置</option>}
              {workspace.embedding_models.map(model => <option key={model}>{model}</option>)}
            </select>
          </label>
          <label className="kw-field">切片策略
            <select value={draft.chunk_strategy} onChange={event => change('chunk_strategy', event.target.value as Profile['chunk_strategy'])}>
              <option value="boundary">{strategyLabels.boundary}</option><option value="fixed">{strategyLabels.fixed}</option>
            </select>
          </label>
          {numeric('chunk_size', '分段长度（字符）', 100, 2000)}
          {numeric('chunk_overlap', '重叠长度（字符）', 0, 99)}
        </div>
      </fieldset>
      <fieldset className="kw-settings-group" disabled={busy || !current}>
        <legend>召回与重排</legend>
        <div className="kw-fields">
          <label className="kw-field">检索模式
            <select value={draft.search_mode} onChange={event => change('search_mode', event.target.value as Profile['search_mode'])}>
              <option value="exact_vector">{searchLabels.exact_vector}</option><option value="vector">{searchLabels.vector}</option>
            </select>
          </label>
          {numeric('top_k', 'Top K', 1, 10)}
          {numeric('score_threshold', '向量相似度下限', 0, 1, 'any')}
          <label className="kw-field">重排模型
            <select value={draft.rerank_model ?? ''} onChange={event => change('rerank_model', event.target.value || null)}>
              <option value="">关闭重排</option>
              {workspace.rerank_models.map(model => <option key={model} value={model}>{model === 'local-lexical' ? '本地词法重排' : model}</option>)}
            </select>
          </label>
          {draft.rerank_model && numeric('rerank_top_n', '重排候选数', 1, 30)}
        </div>
        <p className="muted small">向量相似度下限用于筛选向量结果。启用重排时，从候选片段中按重排结果取 Top K。</p>
      </fieldset>
      {!workspace.embedding_models.length && <p className="error" role="status">请先在后端配置 Embedding 模型，再应用设置或导入文档。</p>}
      <div className="kw-actions">
        <button type="button" className="button secondary" disabled={busy || (current && !changed)} onClick={loadPublished}>载入当前设置</button>
        <button type="submit" className="button primary" disabled={busy || (!changed && !indexNeedsBuild) || !current || !workspace.embedding_models.length}><Save size={16} />{indexNeedsBuild ? '建立索引' : '应用设置'}</button>
      </div>
    </form>
    {confirming && <KnowledgeDialog title="应用知识库设置" busy={saving} onClose={() => setConfirming(false)}>
      <p>{rebuild ? '模型或切片规则已改动，应用时将重建索引。完成后新设置生效。' : '确认后，后续聊天问答和召回测试将使用新设置。'}</p>
      {error && <p className="error" role="alert">{error}</p>}
      {!current && <p className="error" role="alert">请取消本次操作，载入当前设置后重新编辑。</p>}
      {saving && <p role="status">{rebuild ? '正在重建索引…' : '正在应用设置…'}</p>}
      <div className="kw-actions">
        <button type="button" className="button secondary" onClick={() => setConfirming(false)} disabled={saving}>取消</button>
        <button type="button" className="button primary" onClick={() => void save()} disabled={saving || busy || !current}>确认应用</button>
      </div>
    </KnowledgeDialog>}
  </section>;
}
