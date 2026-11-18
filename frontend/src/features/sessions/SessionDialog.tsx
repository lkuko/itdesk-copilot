import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { api } from '../../api/client';
import { errorText } from '../../api/errors';
import { sensitiveInputReason } from '../../api/safety';
import type { SessionSummary } from '../../api/types';

interface Props { session: SessionSummary; onClose: () => void; onChanged: (deleted: boolean) => Promise<void>; onNotice: (message: string) => void }
export default function SessionDialog({ session, onClose, onChanged, onNotice }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [title, setTitle] = useState(session.title);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { dialog.current?.showModal(); }, []);
  async function rename() {
    if (!title.trim()) { setError('会话名称不能为空。'); return; }
    const reason = sensitiveInputReason(title);
    if (reason) { setError(reason); return; }
    setBusy(true); setError('');
    try { await api.renameSession(session.session_id, title.trim()); await onChanged(false); onNotice('会话名称已保存。'); onClose(); }
    catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  async function remove() {
    if (!confirmed) return;
    setBusy(true); setError('');
    try { await api.deleteSession(session.session_id); await onChanged(true); onNotice('会话已删除，已创建工单仍保留。'); onClose(); }
    catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="session-dialog" aria-labelledby="session-dialog-title" onCancel={e => { if (busy) e.preventDefault(); else onClose(); }}><div className="dialog-heading"><h2 id="session-dialog-title">管理会话</h2><button className="icon-button" aria-label="关闭会话管理" onClick={onClose} disabled={busy}><X size={19} /></button></div>{error && <div className="error" role="alert">{error}</div>}<form onSubmit={e => { e.preventDefault(); void rename(); }}><label className="form-field">会话名称<input autoFocus value={title} maxLength={80} onChange={e => setTitle(e.target.value)} disabled={busy} /></label><button type="submit" className="button primary" disabled={busy || !title.trim()}>保存名称</button></form><div className="danger-zone"><h3>删除会话</h3><p className="muted small">删除这段对话、消息及草稿；已创建的工单和审计记录仍然保留。此操作无法从页面恢复。</p><label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} disabled={busy} />我确认删除这段会话</label><button className="button danger" disabled={!confirmed || busy} onClick={() => void remove()}>删除会话</button></div></dialog>;
}
