import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

export function KnowledgeDialog({ title, onClose, children, busy = false, drawer = false }: {
  title: string; onClose: () => void; children: ReactNode; busy?: boolean; drawer?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className={`kw-dialog${drawer ? ' kw-drawer' : ''}`}
    aria-label={title} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className="kw-dialog-heading">
      <h2>{title}</h2>
      <button type="button" className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={19} /></button>
    </header>
    <div className="kw-dialog-body">{children}</div>
  </dialog>;
}
