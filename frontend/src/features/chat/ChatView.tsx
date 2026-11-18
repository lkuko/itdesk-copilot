import { ArrowUp, BookOpen, Check, Clock3, Headphones, Sparkles, Terminal, Wifi, FileText, Printer, Mail, TicketCheck } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AgentResult, Message, SessionDetail } from '../../api/types';
import type { Preferences } from '../settings/preferences';
import type { PendingRequest } from './pending';
import { redactSensitiveText, redactSensitiveValue } from '../../api/safety';
import { DraftList } from '../drafts/DraftList';
import { formatTime } from './formatTime';
const CATEGORY: Record<string, string> = { vpn: 'VPN', email: '邮箱', device: '办公设备', other: '其他' };
const INTENT: Record<string, string> = { troubleshooting: '故障排查', service_status: '查询服务状态', ticket_status: '查询工单', create_ticket: '准备工单', other: '其他咨询' };
const toolLabels: Record<string, string> = { search_knowledge: '检索手册', get_service_status: '查询服务状态', get_ticket_status: '查询工单状态' };
function number(value: unknown) { return typeof value === 'number' && Number.isFinite(value) ? value : 0; }
function Answer({ result, preferences, onPrefill, question }: { result: AgentResult; preferences: Preferences; onPrefill: (value: string) => void; question: string }) {
  const classification = result.classification;
  const traces = Array.isArray(result.tool_trace) ? result.tool_trace.filter(entry => entry && typeof entry === 'object') : [];
  const citations = Array.isArray(result.citations) ? result.citations.filter(citation => citation && typeof citation === 'object') : [];
  const metrics = result.metrics;
  return <>
    {classification && <p className="message-classification muted small">{[CATEGORY[classification.category ?? ''], INTENT[classification.intent ?? '']].filter(Boolean).join(' · ')}</p>}
    {result.status === 'failed' ? <div className="error" role="alert">{redactSensitiveText(result.error?.message || '本次请求未完成，请稍后重试。')} {question && <button className="text-button" onClick={() => onPrefill(question)}>重新提问</button>}</div> : <>
      {result.status === 'no_evidence' && <p className="info">资料不足，暂时无法给出有依据的结论。</p>}
      {result.status === 'needs_info' && <p className="info">需要补充信息后才能继续。</p>}
      <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ a: props => <a {...props} target="_blank" rel="noopener noreferrer" />, img: () => <span className="muted">[图片链接已省略]</span> }}>{redactSensitiveText(String(result.answer || '服务未返回回答内容。'))}</ReactMarkdown></div>
      {!!citations.length && <div className="sources"><h3><BookOpen size={15} />检索到的手册原文</h3>{citations.map((citation, index) => <details key={`${citation.chunk_id}-${index}`} open={preferences.expand_sources}><summary>{String(index + 1).padStart(2, '0')} · {citation.title || citation.doc_id || '资料'}</summary><div className="detail-body"><p className="muted small">来源：{citation.source || '未知'} · 版本：{citation.version || '未知'} · 片段：{citation.chunk_id}</p><pre className="source-content">{redactSensitiveText(String(citation.content || '未返回引用原文'))}</pre></div></details>)}</div>}
      {result.status === 'no_evidence' && <p className="muted small">需要人工处理时，请联系正式 IT 服务台；这里不会代为修复设备或变更权限。</p>}
      {result.status === 'no_evidence' && question && ['vpn', 'email', 'device'].includes(classification?.category || '') && <button className="button secondary small-button" onClick={() => onPrefill(`${question}\n请为这个问题准备人工核查工单草稿，先别提交。`)}>准备人工核查草稿</button>}
    </>}
    <details className="diagnostics" open={preferences.expand_tools}><summary><Terminal size={14} />只读工具与运行记录 · {traces.length} 次请求</summary><div className="detail-body"><p className="muted small">每轮最多 3 次工具尝试；被拒绝的请求不会执行。</p>{!traces.length && <p className="muted small">本轮未调用只读工具。</p>}{traces.map((entry, index) => <div className="tool-entry" key={index}><strong>{String(index + 1).padStart(2, '0')} · {toolLabels[entry.name || ''] || entry.name || '未知工具'} · {['ok', 'success'].includes(entry.status || '') ? '完成' : entry.status === 'rejected' ? '未执行' : '失败'}</strong><p className="muted small">{entry.name} · 只读操作</p><details><summary>查看调用详情</summary><pre>{JSON.stringify(redactSensitiveValue(entry), null, 2)}</pre></details></div>)}{preferences.show_metrics && metrics && <div className="metrics"><div className="metric-row"><span>总耗时<strong>{(number(metrics.total_ms) / 1000).toFixed(2)} 秒</strong></span><span>检索耗时<strong>{(number(metrics.retrieval_ms) / 1000).toFixed(2)} 秒</strong></span><span>生成耗时<strong>{(number(metrics.generation_ms) / 1000).toFixed(2)} 秒</strong></span></div><p className="muted small">输入 {number(metrics.input_tokens).toLocaleString()} / 输出 {number(metrics.output_tokens).toLocaleString()} tokens · Embedding {number(metrics.embedding_tokens)} tokens</p><p className="muted small">{metrics.cost_usd === null || metrics.cost_usd === undefined ? '单价未配置，费用未知。' : `估算费用 $${number(metrics.cost_usd).toFixed(6)}`}</p></div>}</div></details>
  </>;
}
function MessageView({ message, preferences, onPrefill, question }: { message: Message; preferences: Preferences; onPrefill: (value: string) => void; question: string }) {
  return <article className={`message ${message.role}`} aria-label={message.role === 'user' ? '用户消息' : '助手消息'}><div className="message-avatar" aria-hidden="true">{message.role === 'assistant' ? <Headphones size={19} /> : '你'}</div><div className="message-body"><div className="message-meta"><span>{message.role === 'user' ? '你' : 'ITDesk 助手'}</span>{preferences.show_timestamps && message.created_at && <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>}</div>{message.role === 'user' ? <p className="user-content">{redactSensitiveText(message.content)}</p> : <Answer result={message.result ?? { answer: message.content }} preferences={preferences} onPrefill={onPrefill} question={question} />}</div></article>;
}
const examples = [
  { title: '网络 / VPN', Icon: Wifi, items: [['VPN 连接失败', 'VPN-403 一直连接失败，应该怎么排查？'], ['查看 VPN 状态', '现在 VPN 服务正常吗？']] },
  { title: '邮箱 / 办公设备', Icon: Mail, items: [['邮箱反复要求登录', '办公网络切换后，邮箱反复要求登录，应该如何排查？'], ['打印任务卡住', 'DEV-101 打印任务卡住，如何排查？公共队列由谁处理？']] },
  { title: '工单 / 其他咨询', Icon: TicketCheck, items: [['查询我的工单', '帮我查询工单 IT-1001 的处理状态。'], ['了解助手的范围', '公司是否提供员工住房补贴？']] },
];
interface Props {
  detail: SessionDetail | null; loading: boolean; preferences: Preferences; ready: boolean;
  question: string; onQuestion: (value: string) => void; onSubmit: () => void; isSubmitting: boolean;
  pending: PendingRequest | null; phase: string; onRetry: (newId?: boolean) => void; onRefresh: () => Promise<void>;
  onRefreshPending: () => Promise<void>; onRefreshHealth: () => Promise<void>;
  onAbandon: () => void; requestErrorCode?: string; onNotice: (message: string) => void; onError: (message: string) => void;
}
export default function ChatView(props: Props) {
  const { detail, loading, preferences, question, onQuestion, onSubmit, isSubmitting, ready, pending, phase } = props;
  const messages = detail?.messages ?? [];
  const empty = !messages.length;
  const prefill = (value: string) => { onQuestion(value.slice(0, 2000)); props.onNotice('示例已填入输入框，可修改后发送。'); document.getElementById('question-input')?.focus(); };
  let previousQuestion = '';
  const composer = <form className="composer" onSubmit={e => { e.preventDefault(); onSubmit(); }}><div className="composer-row"><textarea id="question-input" aria-label="描述 IT 问题" placeholder="描述 IT 问题，例如 VPN-403 连接失败……" value={question} maxLength={2000} disabled={!ready || isSubmitting || !!pending} onChange={e => onQuestion(e.target.value)} aria-describedby="input-help input-count" rows={2} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (question.trim() && ready && !pending) onSubmit(); } }} /><button className="send-button" type="submit" aria-label="发送问题" title="发送问题" disabled={!question.trim() || !ready || isSubmitting || !!pending}><ArrowUp size={20} /></button></div><p id="input-count" className="input-count">{question.length.toLocaleString()} / 2,000 字</p><div className="composer-meta"><span>手册问答 · 服务状态 · 模拟工单</span><span>只读查询，操作需确认</span></div><p id="input-help" className="muted input-help">请勿输入密码或密钥 · Enter 发送 / Shift+Enter 换行</p></form>;
  return <section className={`chat-page ${empty ? 'is-empty' : ''}`} aria-label="问答助手">
    {loading && <p className="loading-state" role="status"><Clock3 size={16} />正在恢复会话……</p>}
    {empty && !loading ? <div className="welcome"><div className="welcome-mark"><Sparkles size={23} /></div><h1>今天，有什么 IT 问题？</h1><p>从一个具体的问题开始，找到有依据的下一步。</p>{composer}{preferences.show_examples && <section className="recommendations" aria-label="推荐问题"><p className="eyebrow">从一个问题开始</p><div className="example-grid">{examples.map(({ title, Icon, items }) => <div key={title}><h2>{title}</h2>{items.map(([label, text], index) => <button className="example-button" disabled={!ready || !!pending} key={label} onClick={() => prefill(text)}>{title.startsWith('邮箱') && index === 1 ? <Printer size={14} /> : title.startsWith('工单') && index === 1 ? <FileText size={14} /> : <Icon size={14} />}{label}</button>)}</div>)}</div><p className="muted small">点击示例可填入输入框，修改后发送。</p></section>}<p className="boundary-note">模拟资料与工单 · 最多 3 次只读工具尝试 · 创建需按钮确认</p></div> : <>
      {detail && <header className="conversation-heading"><h1>{detail.title}</h1><p className="muted small">会话与草稿保存在本机，刷新后可继续。</p></header>}
      <div className="messages">{messages.map((message, index) => { if (message.role === 'user') previousQuestion = message.content; return <MessageView key={message.message_id ?? index} message={message} preferences={preferences} onPrefill={prefill} question={previousQuestion} />; })}</div>
      {!!detail?.drafts.length && <DraftList drafts={detail.drafts} disabled={isSubmitting} onChanged={props.onRefresh} onNotice={props.onNotice} onError={props.onError} />}
      {composer}
    </>}
    {pending && <section className="pending-panel card" aria-live="polite"><h2>{phase === 'submitting' ? <><Clock3 size={17} />正在查阅资料与模拟系统……</> : <><Clock3 size={17} />原请求结果待确认</>}</h2><p className="muted small">{phase === 'submitting' ? '每轮最多 3 次工具尝试。处理完成前不会创建工单，请稍候。' : props.requestErrorCode === 'request_interrupted' ? '先前请求未完成，无法沿用原编号继续。请先刷新确认，再决定是否以新请求重新提交。' : props.requestErrorCode === 'request_id_conflict' ? '原请求编号已用于不同问题，请先刷新会话。确认要提交当前问题时，可使用新请求编号。' : '超时或离开页面不代表后端已取消。请先刷新会话，确认原请求是否已完成。'}</p>{phase !== 'submitting' && <div className="action-row"><button className="button secondary" onClick={() => void props.onRefreshPending()}>刷新会话</button>{['request_interrupted', 'request_id_conflict'].includes(props.requestErrorCode || '') ? <button className="button primary" onClick={() => props.onRetry(true)}>以新请求重新提交</button> : <button className="button primary" onClick={() => props.onRetry()}>重试原请求</button>}<button className="button secondary" onClick={props.onAbandon}>放弃重试，继续会话</button></div>}</section>}
    {!ready && <p className="warning-box"><Headphones size={15} />服务暂不可用，输入与推荐问题已暂停。<button className="text-button" onClick={() => void props.onRefreshHealth()}>刷新服务状态</button></p>}
    <p className="screen-reader" role="status">{phase === 'success' ? '本轮处理完成，结果已保存。' : ''}</p>
    <span className="screen-reader"><Check size={1} /></span>
  </section>;
}
