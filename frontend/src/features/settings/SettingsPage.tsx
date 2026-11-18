import { useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { Check, RotateCcw, SlidersHorizontal } from 'lucide-react';
import type { Health } from '../../api/types';
import { redactSensitiveText } from '../../api/safety';
import { DEFAULT_PREFERENCES, resetPreferences, savePreferences } from './preferences';
import type { Preferences } from './preferences';

interface Props { preferences: Preferences; onApply: (values: Preferences) => void; health: Health | null }
const TABS = [['general', '通用'], ['chat', '聊天显示'], ['about', '关于与状态']] as const;
export default function SettingsPage({ preferences, onApply, health }: Props) {
  const [draft, setDraft] = useState<Preferences>({ ...preferences });
  const [tab, setTab] = useState<typeof TABS[number][0]>('general');
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  function change<K extends keyof Preferences>(key: K, value: Preferences[K]) { setDraft(current => ({ ...current, [key]: value })); setMessage(''); }
  function save() { try { const values = savePreferences(draft); onApply(values); setError(''); setMessage('设置已保存，刷新后仍会保留。'); } catch (e) { setError(e instanceof Error ? e.message : '设置保存失败。'); } }
  function reset() { try { const values = resetPreferences(); setDraft(values); onApply(values); setError(''); setMessage('已恢复默认设置，会话、草稿与工单均保留。'); } catch (e) { setError(e instanceof Error ? e.message : '设置恢复失败。'); } }
  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    let next: number;
    switch (event.key) {
      case 'ArrowRight': next = (index + 1) % TABS.length; break;
      case 'ArrowLeft': next = (index + TABS.length - 1) % TABS.length; break;
      case 'Home': next = 0; break;
      case 'End': next = TABS.length - 1; break;
      default: return;
    }
    event.preventDefault();
    setTab(TABS[next][0]);
    tabButtons.current[next]?.focus();
  }
  const toggles: [keyof Preferences, string, string?][] = [
    ['show_examples', '在新会话显示推荐问题'], ['show_timestamps', '显示消息时间'],
    ['show_metrics', '显示耗时、Token 与费用', '关闭后仍可查看工具步骤与错误；费用未知时不会显示为零。'],
    ['expand_sources', '展开引用原文'], ['expand_tools', '展开只读工具与运行记录'],
  ];
  return <section className="settings-page page-stack" aria-labelledby="settings-title">
    <header className="page-heading"><span className="eyebrow"><SlidersHorizontal size={14} /> 为你调整</span><h1 id="settings-title">设置</h1><p>把工作台调成你习惯的样子。偏好只影响显示，不改变模型与工单规则。</p></header>
    <div role="tablist" aria-label="设置分类" aria-orientation="horizontal" className="tabs">
      {TABS.map(([id, label], index) => <button key={id} ref={element => { tabButtons.current[index] = element; }} id={`tab-${id}`} type="button" role="tab" tabIndex={tab === id ? 0 : -1} aria-selected={tab === id} aria-controls={`panel-${id}`} onClick={() => setTab(id)} onKeyDown={event => navigateTabs(event, index)}>{label}</button>)}
    </div>
    {error && <div className="error" role="alert">{error}</div>}{message && <div className="notice" role="status"><Check size={17} />{message}</div>}
    <form onSubmit={e => { e.preventDefault(); save(); }}>
      <section role="tabpanel" id="panel-general" aria-labelledby="tab-general" hidden={tab !== 'general'} className="settings-panel">
        <h2>外观</h2><fieldset className="theme-options"><legend>界面主题</legend>
          {[['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']].map(([value, label]) => <label key={value}><input type="radio" name="theme" value={value} checked={draft.theme === value} onChange={() => change('theme', value as Preferences['theme'])} /><span>{label}</span></label>)}
        </fieldset>
        <div className="field-grid"><label className="form-field">正文字号<select value={draft.font_size} onChange={e => change('font_size', e.target.value as Preferences['font_size'])}><option value="small">小</option><option value="standard">标准</option><option value="large">大</option></select></label>
        <label className="form-field">阅读密度<select value={draft.density} onChange={e => change('density', e.target.value as Preferences['density'])}><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></label></div>
        <p className="info">偏好保存在当前浏览器。点击保存才应用，刷新后可恢复；不同浏览器不自动同步。</p>
      </section>
      <section role="tabpanel" id="panel-chat" aria-labelledby="tab-chat" hidden={tab !== 'chat'} className="settings-panel"><h2>对话偏好</h2>
        {toggles.map(([key, label, help], index) => <div key={key} className={index === 3 ? 'toggle-row section-break' : 'toggle-row'}><label><input type="checkbox" role="switch" checked={Boolean(draft[key])} onChange={e => change(key, e.target.checked)} /><span>{label}</span></label>{help && <p className="muted small">{help}</p>}</div>)}
      </section>
      <section role="tabpanel" id="panel-about" aria-labelledby="tab-about" hidden={tab !== 'about'} className="settings-panel"><h2>ITDesk Copilot</h2><p>面向 IT 问题的本机演示助手，使用模拟手册、服务快照与工单。</p><p className="muted">单个 Agent · 3 个只读工具 · 每轮最多 3 次工具尝试</p><p>创建工单必须由按钮确认。助手没有真实权限开通或设备修复能力。</p><div className="card health-details"><span className={`badge ${health?.status === 'ok' ? 'success' : 'warning'}`}>{health?.status === 'ok' ? '本地状态已就绪' : '本地服务待检查'}</span><p>知识库：{health?.index?.document_count ?? '—'} 篇手册 · {health?.index?.chunk_count ?? '—'} 个片段</p>{health?.index?.updated_at && <p className="muted small">索引更新时间：{health.index.updated_at}</p>}{health?.models?.chat && <p className="muted small">聊天模型：{redactSensitiveText(health.models.chat)}</p>}{health?.models?.embedding && <p className="muted small">Embedding 模型：{redactSensitiveText(health.models.embedding)}</p>}<p className="muted small">此处检查本地配置和索引，不代表远端模型实时可用。密钥与服务地址不在页面编辑。</p></div><details><summary>使用帮助与操作边界</summary><p>提供具体症状、错误码和已尝试步骤。引用卡片可展开查看原文。准备草稿后，先保存修改，再点击确认创建。</p></details><p className="warning-box">消息、草稿和工单保存在本机明文数据库。请只使用模拟数据，不要填写真实凭证或个人敏感资料。</p></section>
      <div className="settings-actions"><button className="button primary" type="submit"><Check size={16} />保存设置</button><button className="button secondary" type="button" onClick={reset}><RotateCcw size={15} />恢复默认设置</button></div><p className="muted small">只恢复 {Object.keys(DEFAULT_PREFERENCES).length} 项显示偏好，不会清空会话、草稿或工单。</p>
    </form>
  </section>;
}
