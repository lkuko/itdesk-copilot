import { useState } from 'react';
import { FileText, UploadCloud } from 'lucide-react';
import { ApiError, errorText } from '../../api/errors';
import { sensitiveInputReason } from '../../api/safety';
import { DocumentContent } from './DocumentContent';
import { KnowledgeDialog } from './KnowledgeDialog';
import { knowledgeApi, type DocumentPreview, type ImportFile, type Workspace } from './workspaceApi';

async function readDocuments(selection: File[]): Promise<ImportFile[]> {
  if (!selection.length || selection.length > 5) throw new Error('请选择 1 到 5 个文档。');
  const names = new Set<string>();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const files: ImportFile[] = [];
  for (const file of selection) {
    const name = file.name;
    if (!/\.(md|txt)$/i.test(name) || /[\\/:]/.test(name)) throw new Error('只接受 .md 和 .txt 文件。');
    if (name.length > 110) throw new Error('文件名最多 110 个字符。');
    if (file.size > 48_000 || file.size === 0) throw new Error(`${name} 为空或超过 48 KB。`);
    if (names.has(name.toLowerCase())) throw new Error('选择了同名文件，请移除重复项。');
    let content: string;
    try { content = decoder.decode(await file.arrayBuffer()); }
    catch { throw new Error(`${name} 无法读取为 UTF-8 文本。`); }
    if (!content.trim() || content.includes('\0')) throw new Error(`${name} 没有可导入的文本。`);
    if (sensitiveInputReason(content) || sensitiveInputReason(name)) {
      throw new Error('文档包含疑似凭证，请清理后重新选择。');
    }
    names.add(name.toLowerCase());
    files.push({ name, content });
  }
  return files;
}

export function ImportWizard({ workspace, onClose, onApply, onRefresh }: {
  workspace: Workspace;
  onClose: () => void;
  onApply: (files: ImportFile[]) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const [files, setFiles] = useState<ImportFile[]>([]);
  const [preview, setPreview] = useState<{ revision: string; documents: DocumentPreview[] }>();
  const [error, setError] = useState('');
  const [pending, setPending] = useState<'read' | 'preview' | 'apply' | 'refresh'>();
  const [mustRefresh, setMustRefresh] = useState(false);
  const ready = preview?.revision === workspace.revision;
  const step = pending === 'apply' ? 3 : preview ? 2 : 1;

  async function selectFiles(selection: File[]) {
    if (pending) return;
    setPending('read');
    setFiles([]);
    setPreview(undefined);
    setError('');
    try { setFiles(await readDocuments(selection)); }
    catch (failure) { setError(failure instanceof Error ? failure.message : '无法读取文档。'); }
    finally { setPending(undefined); }
  }

  async function previewFiles() {
    if (!files.length || pending || mustRefresh) return;
    setPending('preview');
    setError('');
    try {
      const result = await knowledgeApi.previewImport(workspace.profile, files);
      setPreview({ ...result, revision: workspace.revision });
    } catch (failure) { setError(errorText(failure)); }
    finally { setPending(undefined); }
  }

  async function apply() {
    if (!ready || pending || mustRefresh) return;
    setPending('apply');
    setError('');
    try { await onApply(files); onClose(); }
    catch (failure) {
      setError(errorText(failure));
      if (failure instanceof ApiError && ['workspace_conflict', 'request_timeout', 'network_error', 'invalid_response'].includes(failure.code)) {
        setPreview(undefined);
        setMustRefresh(true);
      }
    } finally { setPending(undefined); }
  }

  async function refresh() {
    setPending('refresh');
    setError('');
    try { await onRefresh(); setMustRefresh(false); }
    catch (failure) { setError(errorText(failure)); }
    finally { setPending(undefined); }
  }

  return <KnowledgeDialog title="导入文档" onClose={onClose} busy={Boolean(pending)}>
    <ol className="kw-steps" aria-label="导入步骤">
      {['选择文件', '预览切片', '建立索引'].map((label, index) =>
        <li key={label} aria-current={index + 1 === step ? 'step' : undefined}>{label}</li>)}
    </ol>
    {error && <p className="error" role="alert">{error}</p>}
    {!preview && <>
      <label className="kw-file-picker" htmlFor="knowledge-files"><UploadCloud size={26} /><strong>选择文档</strong>
        <span>UTF-8 的 .md 或 .txt，每次 1–5 个，每个最多 48 KB。</span>
      </label>
      <input id="knowledge-files" aria-label="选择文档" className="kw-file-input" type="file"
        accept=".md,.txt,text/plain,text/markdown" multiple disabled={Boolean(pending)}
        onChange={event => {
          if (event.target.files?.length) void selectFiles(Array.from(event.target.files));
          event.target.value = '';
        }} />
      {files.length > 0 && <ul className="kw-file-list">
        {files.map(file => <li key={file.name}><FileText size={16} />{file.name}</li>)}
      </ul>}
    </>}
    {preview && <>
      <p className="muted">本批文件将使用已生效的切片规则。确认后会更新问答和召回测试使用的索引。</p>
      {!ready && <p className="error" role="alert">知识库版本已更新，请重新预览切片。</p>}
      <div className="kw-import-preview">{preview.documents.map(document =>
        <DocumentContent key={document.doc_id} document={document} showFirstChunk />)}</div>
    </>}
    {pending && <p role="status">{
      { read: '正在读取文档…', preview: '正在计算切片…', apply: '正在建立索引…', refresh: '正在读取当前版本…' }[pending]
    }</p>}
    <div className="kw-actions">
      <button type="button" className="button secondary" onClick={onClose} disabled={Boolean(pending)}>取消</button>
      {mustRefresh ? <button type="button" className="button primary" disabled={Boolean(pending)}
        onClick={() => void refresh()}>刷新当前版本</button>
        : preview && ready ? <>
          <button type="button" className="button secondary" disabled={Boolean(pending)} onClick={() => setPreview(undefined)}>返回选择文件</button>
          <button type="button" className="button primary" onClick={() => void apply()} disabled={Boolean(pending)}>确认并建立索引</button>
        </> : <button type="button" className="button primary" onClick={() => void previewFiles()}
          disabled={!files.length || Boolean(pending)}>预览切片</button>}
    </div>
  </KnowledgeDialog>;
}
