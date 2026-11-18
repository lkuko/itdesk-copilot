import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../../api/client';
import { errorText } from '../../api/errors';
import type { Draft } from '../../api/types';
import './drafts.css';

type DraftListProps = {
  drafts: Draft[];
  disabled?: boolean;
  onChanged: () => void | Promise<void>;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
};

const categories = { vpn: 'VPN', email: '邮箱', device: '办公设备' } as const;
const statuses = { pending: '待确认', confirmed: '已创建工单', cancelled: '已取消' } as const;

function savedTime(value?: string) {
  if (!value) return '时间未记录';
  const normalized = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(value)
    ? value.replace(' ', 'T') + 'Z' : value;
  const time = new Date(normalized);
  return Number.isNaN(time.getTime()) ? '时间未记录' : time.toLocaleString('zh-CN', { hour12: false });
}

function DraftCard({ draft, disabled, onChanged, onNotice, onError }: Omit<DraftListProps, 'drafts'> & { draft: Draft }) {
  const [record, setRecord] = useState(draft);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(draft.title);
  const [description, setDescription] = useState(draft.description);
  const [category, setCategory] = useState(draft.category);
  const [operation, setOperation] = useState<'save' | 'confirm' | 'cancel' | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const operationLock = useRef(false);

  useEffect(() => {
    setRecord(draft);
    // A refreshed persisted row wins over stale input if another interface finished it.
    if (draft.status !== 'pending') { setEditing(false); setUncertain(false); }
  }, [draft]);

  const busy = Boolean(disabled || operation);
  const pending = record.status === 'pending';

  function beginEditing() {
    setTitle(record.title);
    setDescription(record.description);
    setCategory(record.category);
    setEditing(true);
  }

  async function refreshAfterWrite(message: string) {
    onNotice(message);
    try { await onChanged(); }
    catch { onError('操作已保存，但会话刷新失败；请刷新查看最新记录。'); }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || uncertain || operationLock.current || !title.trim() || !description.trim()) return;
    operationLock.current = true;
    setOperation('save');
    try {
      const result = await api.updateDraft(record.draft_id, {
        title: title.trim(), description: description.trim(), category,
      });
      setRecord(result.draft);
      setEditing(false);
      setUncertain(false);
      await refreshAfterWrite('草稿已保存，仍需单独确认后才会创建工单。');
    } catch (error) {
      // The PATCH may already have committed. Do not allow confirmation of the
      // old displayed fields until the persisted content has been checked.
      setUncertain(true);
      onError(`${errorText(error)} 草稿修改结果尚未确认，请刷新状态并核对实际内容。`);
    } finally { operationLock.current = false; setOperation(null); }
  }

  async function act(action: 'confirm' | 'cancel') {
    if (busy || operationLock.current || editing || !pending) return;
    operationLock.current = true;
    setOperation(action);
    try {
      if (action === 'confirm') {
        const result = await api.confirmDraft(record.draft_id);
        setRecord({ ...result.draft, ticket_id: result.ticket.ticket_id });
        setUncertain(false);
        await refreshAfterWrite(`模拟工单 ${result.ticket.ticket_id} 已创建。`);
      } else {
        const result = await api.cancelDraft(record.draft_id);
        setRecord(result.draft);
        setUncertain(false);
        await refreshAfterWrite('草稿已取消，不会创建工单。');
      }
    } catch (error) {
      // A lost response may follow a successful transaction. Refresh or retry the
      // persisted identifier; never infer cancellation or creation from the timeout.
      setUncertain(true);
      onError(`${errorText(error)} 操作结果尚未确认，请刷新草稿状态。`);
    } finally { operationLock.current = false; setOperation(null); }
  }

  async function refreshRecord() {
    if (operationLock.current || busy) return;
    operationLock.current = true;
    setOperation('save');
    try {
      const latest = await api.getDraft(record.draft_id);
      // getDraft has no ticket join; keep ticket_id from the session when known.
      setRecord({ ...latest, ticket_id: record.ticket_id });
      setEditing(false);
      setUncertain(false);
      await onChanged();
    } catch (error) { onError(errorText(error)); }
    finally { operationLock.current = false; setOperation(null); }
  }

  return (
    <article className="card draft-card" aria-label={`工单草稿 ${record.title}`} aria-busy={Boolean(operation)}>
      <div className="draft-card-heading">
        <span className={`badge draft-status-${record.status}`}>{statuses[record.status] ?? record.status}</span>
        <small>普通优先级 · 供人工核对</small>
      </div>
      {editing ? (
        <form onSubmit={(event) => void save(event)} className="draft-editor">
          <label className="form-field">草稿标题
            <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} required disabled={busy} />
          </label>
          <label className="form-field">问题分类
            <select value={category} onChange={(event) => setCategory(event.target.value as Draft['category'])} disabled={busy}>
              {Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="form-field">问题描述
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={2000} rows={5} required disabled={busy} />
          </label>
          <p className="muted draft-note">保存修改只更新草稿，不会创建工单。请勿填写密码或密钥。</p>
          <div className="action-row">
            <button className="button primary" disabled={busy || uncertain || !title.trim() || !description.trim()} type="submit">{operation === 'save' ? '正在保存…' : '保存草稿'}</button>
            <button className="button secondary" disabled={busy} type="button" onClick={() => setEditing(false)}>放弃修改</button>
          </div>
        </form>
      ) : (
        <>
          <h3>{record.title}</h3>
          <p className="draft-description">{record.description}</p>
          <p className="muted draft-note">分类：{categories[record.category] ?? record.category} · 用户陈述未经独立核实</p>
          <small className="muted">草稿保存在本机 · {savedTime(record.updated_at || record.created_at)}</small>
          {pending && <div className="action-row draft-actions">
            <button className="button secondary" type="button" disabled={busy || uncertain} onClick={beginEditing}>修改草稿</button>
            <button className="button primary" type="button" disabled={busy || uncertain} onClick={() => void act('confirm')}>{operation === 'confirm' ? '正在确认…' : '确认创建'}</button>
            <button className="button secondary" type="button" disabled={busy || uncertain} onClick={() => void act('cancel')}>{operation === 'cancel' ? '正在取消…' : '取消草稿'}</button>
          </div>}
        </>
      )}
      {uncertain && <div role="status" className="draft-recovery">
        <p>请先核对服务器中的草稿状态和内容，避免按旧内容确认或重复操作。</p>
        <button type="button" className="button secondary" disabled={busy} onClick={() => void refreshRecord()}>刷新草稿状态</button>
      </div>}
      {record.status === 'confirmed' && <p className="draft-confirmed" role="status">{record.ticket_id ? `工单 ${record.ticket_id} · 已确认` : '已确认创建；刷新会话查看工单编号。'}</p>}
      {record.status === 'cancelled' && <p className="muted">已取消，不会创建工单。</p>}
    </article>
  );
}

export function DraftList(props: DraftListProps) {
  if (!props.drafts.length) return null;
  return <section className="draft-list" aria-label="工单草稿">
    <div><h2>工单草稿</h2><p className="muted">核对或修改后，再确认创建模拟工单。</p></div>
    {props.drafts.map((draft) => <DraftCard key={draft.draft_id} {...props} draft={draft} />)}
  </section>;
}

export default DraftList;
