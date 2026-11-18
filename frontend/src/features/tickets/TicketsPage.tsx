import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { errorText } from '../../api/errors';
import type { Ticket } from '../../api/types';
import './tickets.css';

const PAGE_SIZE = 20;
const categories = { vpn: 'VPN', email: '邮箱', device: '办公设备' } as const;
const statuses: Record<string, string> = { open: '待处理', in_progress: '处理中', resolved: '已解决', closed: '已关闭' };

function ticketTime(value?: string) {
  if (!value) return '—';
  const parsed = new Date(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(value) ? value.replace(' ', 'T') + 'Z' : value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('zh-CN', { hour12: false });
}

export function TicketsPage() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError(null);
    // Each page is independent. A slow older page cannot replace the selected page.
    api.listTickets(PAGE_SIZE, offset).then((result) => {
      if (current) setTickets(result.tickets);
    }).catch((failure: unknown) => {
      if (current) { setError(errorText(failure)); setTickets([]); }
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [offset, version]);

  return <section className="tickets-page" aria-labelledby="tickets-title">
    <div className="tickets-heading">
      <div><h1 id="tickets-title">历史工单</h1><p className="muted">查看当前演示用户的模拟工单和处理状态。</p></div>
      <button type="button" className="button secondary" disabled={loading} onClick={() => setVersion((value) => value + 1)}>刷新工单</button>
    </div>
    {loading && <p role="status">正在加载工单…</p>}
    {error && <div className="error" role="alert"><p>{error}</p><button className="button secondary" type="button" onClick={() => setVersion((value) => value + 1)}>重试加载工单</button></div>}
    {!loading && !error && !tickets.length && <div className="empty-state"><h2>暂无工单</h2><p>已确认的模拟工单会显示在这里。草稿不会自动创建工单。</p></div>}
    {!loading && !error && tickets.map((ticket) => <article key={ticket.ticket_id} className="card ticket-card" aria-label={`工单 ${ticket.ticket_id}`}>
      <div className="ticket-card-heading"><span className="ticket-id">{ticket.ticket_id}</span><span className="badge">{statuses[ticket.status] ?? ticket.status}</span></div>
      <h2>{ticket.title}</h2>
      <div className="ticket-meta muted"><span>{categories[ticket.category as keyof typeof categories] ?? ticket.category}</span><span>优先级：{ticket.priority === 'normal' ? '普通' : ticket.priority}</span><span>更新时间：{ticketTime(ticket.updated_at || ticket.created_at)}</span></div>
      <details><summary>查看问题描述</summary><p className="ticket-description">{ticket.description}</p></details>
    </article>)}
    <div className="action-row ticket-pagination" aria-label="工单分页">
      <button className="button secondary" type="button" disabled={loading || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>较新工单</button>
      <span className="muted">第 {Math.floor(offset / PAGE_SIZE) + 1} 页</span>
      <button className="button secondary" type="button" disabled={loading || Boolean(error) || tickets.length < PAGE_SIZE} onClick={() => setOffset((value) => value + PAGE_SIZE)}>较早工单</button>
    </div>
    <p className="muted ticket-footnote">这些工单来自本机模拟数据库，状态不代表真实 IT 服务进度。</p>
  </section>;
}

export default TicketsPage;
