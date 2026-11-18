import { useEffect, useState } from 'react';
import { ChevronDown, FileText, Plus, Search, Trash2 } from 'lucide-react';
import { errorText } from '../../api/errors';
import { DocumentContent } from './DocumentContent';
import { ImportWizard } from './ImportWizard';
import { KnowledgeDialog } from './KnowledgeDialog';
import { knowledgeApi, type Catalog, type DocumentPreview, type DocumentSummary, type ImportFile, type Workspace } from './workspaceApi';

export function DocumentsPanel({ catalog, workspace, busy, onApply, onRefresh }: {
  catalog?: Catalog;
  workspace?: Workspace;
  busy: boolean;
  onApply: (files: ImportFile[], removeIds: string[]) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const [filter, setFilter] = useState('');
  const [selected, setSelected] = useState<string>();
  const [preview, setPreview] = useState<DocumentPreview>();
  const [previewError, setPreviewError] = useState('');
  const [importing, setImporting] = useState(false);
  const [removing, setRemoving] = useState<{ document: DocumentSummary; revision: string }>();
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState('');

  useEffect(() => {
    if (!selected) return;
    let current = true;
    setPreview(undefined);
    setPreviewError('');
    knowledgeApi.previewDocument(selected).then(data => {
      if (current) setPreview(data);
    }).catch(failure => {
      if (current) setPreviewError(errorText(failure));
    });
    return () => { current = false; };
  }, [selected, workspace?.revision]);

  async function remove() {
    if (!removing || removing.revision !== workspace?.revision || removeBusy) return;
    setRemoveBusy(true);
    setRemoveError('');
    try { await onApply([], [removing.document.doc_id]); setRemoving(undefined); }
    catch (failure) { setRemoveError(errorText(failure)); }
    finally { setRemoveBusy(false); }
  }

  const search = filter.trim().toLowerCase();
  const documents = catalog?.documents ?? [];
  const filtered = documents.filter(document =>
    [document.doc_id, document.title, document.source].some(value => value.toLowerCase().includes(search)));
  const managed = new Set(workspace?.managed_ids);
  const canEdit = Boolean(workspace?.embedding_models.length) && !busy;
  return <section className="kw-card" aria-labelledby="kw-documents">
    <div className="kw-card-heading">
      <div>
        <h2 id="kw-documents">文档列表</h2>
        <p className="muted">查看手册原文及切片，或补充知识库资料。</p>
      </div>
      <button type="button" className="button primary" disabled={!canEdit} onClick={() => setImporting(true)}>
        <Plus size={17} />导入文档
      </button>
    </div>
    <div className="kw-document-toolbar">
      <label className="kw-search"><Search size={16} /><span className="screen-reader">搜索标题、编号或文件</span>
        <input type="search" value={filter} placeholder="搜索标题、编号或文件" onChange={event => setFilter(event.target.value)} />
      </label>
      <span className="muted small">{catalog ? `显示 ${filtered.length} / ${documents.length} 篇` : '文档目录'}</span>
    </div>
    {catalog && (filtered.length ? <div className="kw-document-list" aria-label="文档">
      {filtered.map(document => {
        const isManaged = managed.has(document.doc_id);
        return <details className="kw-document-item" key={document.doc_id}>
          <summary className="kw-document-summary">
            <span className="kw-document-summary-main">
              <span className="kw-document-icon"><FileText size={17} /></span>
              <span className="kw-document-summary-copy">
                <strong>{document.title}</strong>
                <span className="kw-document-summary-meta">
                  <span className="kw-origin">{isManaged ? '已导入' : '内置手册'}</span>
                  <span className="muted small">{document.doc_id}</span>
                </span>
              </span>
            </span>
            <span className="kw-document-summary-stats">
              <span>{document.chunk_count} 个片段</span>
              <ChevronDown size={17} aria-hidden="true" />
            </span>
          </summary>
          <div className="kw-document-details">
            <dl className="kw-document-facts">
              <div><dt>来源</dt><dd>{document.source}</dd></div>
              <div><dt>版本</dt><dd>{document.version}</dd></div>
              <div><dt>片段</dt><dd>{document.chunk_count}</dd></div>
            </dl>
            <div className="kw-document-actions">
              <button type="button" className="button secondary" onClick={() => setSelected(document.doc_id)}>
                <FileText size={16} />查看原文与切片
              </button>
              {isManaged ? <button type="button" className="icon-button kw-remove"
                title="移除文档" aria-label={`移除 ${document.title}`} disabled={!canEdit}
                onClick={() => { if (workspace) setRemoving({ document, revision: workspace.revision }); setRemoveError(''); }}>
                <Trash2 size={16} />
              </button> : <span className="muted small">内置文档不可移除</span>}
            </div>
          </div>
        </details>;
      })}
    </div> : <p className="kw-empty muted">{filter ? '没有匹配的手册。' : '还没有可预览的手册。'}</p>)}

    {selected && <KnowledgeDialog title="原文与切片" onClose={() => { setSelected(undefined); setPreview(undefined); }} drawer>
      {previewError ? <p className="error" role="alert">{previewError}</p>
        : preview ? <DocumentContent document={preview} /> : <p role="status">正在读取手册…</p>}
    </KnowledgeDialog>}

    {importing && workspace && <ImportWizard workspace={workspace} onClose={() => setImporting(false)}
      onApply={files => onApply(files, [])} onRefresh={onRefresh} />}

    {removing && <KnowledgeDialog title="移除文档" busy={removeBusy} onClose={() => setRemoving(undefined)}>
      <p>移除「{removing.document.title}」将更新索引，之后的问答和召回测试将不再检索这篇文档。</p>
      {removeError && <p className="error" role="alert">{removeError}</p>}
      {removing.revision !== workspace?.revision && <p className="error" role="alert">知识库已更新，请关闭后重新选择文档。</p>}
      {removeBusy && <p role="status">正在更新索引…</p>}
      <div className="kw-actions">
        <button type="button" className="button secondary" disabled={removeBusy} onClick={() => setRemoving(undefined)}>取消</button>
        <button type="button" className="button danger" disabled={removeBusy || busy || removing.revision !== workspace?.revision}
          onClick={() => void remove()}>确认移除</button>
      </div>
    </KnowledgeDialog>}
  </section>;
}
