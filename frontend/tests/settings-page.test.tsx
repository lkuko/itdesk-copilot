import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import SettingsPage from '../src/features/settings/SettingsPage';
import { DEFAULT_PREFERENCES, loadPreferences, PREFERENCES_KEY, savePreferences } from '../src/features/settings/preferences';

describe('settings form commit boundary', () => {
  it('supports keyboard tab navigation with one focusable tab and focus inside the active panel', async () => {
    const user = userEvent.setup();
    render(<SettingsPage preferences={DEFAULT_PREFERENCES} onApply={vi.fn()} health={null} />);
    const general = screen.getByRole('tab', { name: '通用' });
    const chat = screen.getByRole('tab', { name: '聊天显示' });
    const about = screen.getByRole('tab', { name: '关于与状态' });
    expect(general).toHaveAttribute('tabindex', '0');
    expect(chat).toHaveAttribute('tabindex', '-1');
    expect(about).toHaveAttribute('tabindex', '-1');
    general.focus();
    await user.keyboard('{ArrowLeft}');
    expect(about).toHaveFocus();
    expect(about).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{ArrowRight}');
    expect(general).toHaveFocus();
    await user.keyboard('{End}');
    expect(about).toHaveFocus();
    await user.keyboard('{Home}');
    expect(general).toHaveFocus();
    await user.keyboard('{ArrowRight}');
    expect(chat).toHaveFocus();
    expect(chat).toHaveAttribute('aria-selected', 'true');
    expect(chat).toHaveAttribute('tabindex', '0');
    expect(general).toHaveAttribute('tabindex', '-1');
    expect(about).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('tabpanel', { name: '聊天显示' })).toBeVisible();
    await user.tab();
    expect(screen.getByRole('switch', { name: '在新会话显示推荐问题' })).toHaveFocus();
  });

  it('keeps all edits unapplied until Save, then persists all eight fields', async () => {
    const user = userEvent.setup();
    const apply = vi.fn();
    render(<SettingsPage preferences={DEFAULT_PREFERENCES} onApply={apply} health={null} />);
    await user.click(screen.getByRole('radio', { name: '深色' }));
    await user.selectOptions(screen.getByRole('combobox', { name: '正文字号' }), 'large');
    await user.selectOptions(screen.getByRole('combobox', { name: '阅读密度' }), 'compact');
    await user.click(screen.getByRole('tab', { name: '聊天显示' }));
    await user.click(screen.getByRole('switch', { name: '在新会话显示推荐问题' }));
    for (const name of ['显示消息时间', '显示耗时、Token 与费用', '展开引用原文', '展开只读工具与运行记录']) {
      await user.click(screen.getByRole('switch', { name }));
    }
    expect(apply).not.toHaveBeenCalled();
    expect(localStorage.getItem(PREFERENCES_KEY)).toBeNull();
    await user.click(screen.getByRole('button', { name: '保存设置' }));
    const selected = { theme: 'dark', font_size: 'large', density: 'compact', show_examples: false,
      show_timestamps: true, show_metrics: true, expand_sources: true, expand_tools: true };
    expect(apply).toHaveBeenCalledExactlyOnceWith(selected);
    expect(loadPreferences()).toEqual(selected);
    expect(screen.getByRole('status')).toHaveTextContent('设置已保存');
  });

  it('does not apply new values if browser storage rejects the write', async () => {
    savePreferences(DEFAULT_PREFERENCES);
    const before = localStorage.getItem(PREFERENCES_KEY);
    const user = userEvent.setup();
    const apply = vi.fn();
    render(<SettingsPage preferences={DEFAULT_PREFERENCES} onApply={apply} health={null} />);
    await user.click(screen.getByRole('radio', { name: '深色' }));
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Quota', 'QuotaExceededError'); });
    await user.click(screen.getByRole('button', { name: '保存设置' }));
    expect(screen.getByRole('alert')).toHaveTextContent('无法保存设置');
    expect(apply).not.toHaveBeenCalled();
    expect(localStorage.getItem(PREFERENCES_KEY)).toBe(before);
  });

  it('restores display defaults while retaining unrelated local and pending data', async () => {
    const user = userEvent.setup();
    const apply = vi.fn();
    localStorage.setItem('business-fixture-marker', 'preserve');
    sessionStorage.setItem('itdesk.pending.v1', 'preserve-pending');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<SettingsPage preferences={{ ...DEFAULT_PREFERENCES, theme: 'dark' }} onApply={apply} health={null} />);
    await user.click(screen.getByRole('button', { name: '恢复默认设置' }));
    expect(apply).toHaveBeenCalledExactlyOnceWith(DEFAULT_PREFERENCES);
    expect(localStorage.getItem('business-fixture-marker')).toBe('preserve');
    expect(sessionStorage.getItem('itdesk.pending.v1')).toBe('preserve-pending');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('会话、草稿与工单均保留');
  });

  it('keeps the About view read only and does not invent provider readiness', async () => {
    const user = userEvent.setup();
    render(<SettingsPage preferences={DEFAULT_PREFERENCES} onApply={vi.fn()} health={null} />);
    await user.click(screen.getByRole('tab', { name: '关于与状态' }));
    expect(screen.getByText('本地服务待检查')).toBeVisible();
    expect(screen.getByText(/不代表远端模型实时可用/)).toBeVisible();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/API Key/i)).not.toBeInTheDocument();
  });
});
