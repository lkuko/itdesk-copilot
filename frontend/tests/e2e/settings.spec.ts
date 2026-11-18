import { expect, test } from '@playwright/test';
import { mockApi, SESSION_ID } from './mock-api';

test('all eight settings apply only on save, restore after reload and reset without business writes', async ({ page }) => {
  const state = await mockApi(page, { history: true, draft: true });
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeVisible();
  await page.getByRole('radio', { name: '深色', exact: true }).check();
  await page.getByRole('combobox', { name: '正文字号', exact: true }).selectOption('large');
  await page.getByRole('combobox', { name: '阅读密度', exact: true }).selectOption('compact');
  await page.getByRole('tab', { name: '聊天显示', exact: true }).click();
  await page.getByRole('switch', { name: '在新会话显示推荐问题', exact: true }).uncheck();
  for (const name of ['显示消息时间', '显示耗时、Token 与费用', '展开引用原文', '展开只读工具与运行记录']) {
    await page.getByRole('switch', { name, exact: true }).check();
  }
  expect(await page.evaluate(() => localStorage.getItem('itdesk.preferences.v1'))).toBeNull();
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('设置已保存');
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('itdesk.preferences.v1') || 'null'));
  expect(stored).toEqual({ version: 1, values: { theme: 'dark', font_size: 'large', density: 'compact',
    show_examples: false, show_timestamps: true, show_metrics: true, expand_sources: true, expand_tools: true } });
  await page.reload();
  await expect(page.getByRole('radio', { name: '深色', exact: true })).toBeChecked();
  await page.goto(`/chat?session=${SESSION_ID}`);
  await expect(page.getByText('VPN-403 怎么处理？', { exact: true })).toBeVisible();
  await expect(page.getByText('单价未配置，费用未知。', { exact: true })).toBeVisible();
  await expect(page.getByText('设备授权由人工核实。助手不能开通授权或修改白名单。', { exact: true })).toBeVisible();
  await expect(page.locator('time')).not.toHaveCount(0);
  const opened = await page.locator('details[open]').count();
  expect(opened).toBeGreaterThanOrEqual(2);
  await page.goto('/chat');
  await expect(page.getByRole('button', { name: 'VPN 连接失败', exact: true })).toHaveCount(0);
  await page.goto('/settings');
  await page.getByRole('button', { name: '恢复默认设置', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('会话、草稿与工单均保留');
  const reset = await page.evaluate(() => JSON.parse(localStorage.getItem('itdesk.preferences.v1') || 'null'));
  expect(reset.values).toEqual({ theme: 'system', font_size: 'standard', density: 'comfortable',
    show_examples: true, show_timestamps: false, show_metrics: false, expand_sources: false, expand_tools: false });
  expect(state.sessions[SESSION_ID].messages).toHaveLength(2);
  expect(state.sessions[SESSION_ID].drafts).toHaveLength(1);
  expect(state.tickets).toHaveLength(1);
  expect(state.requests.filter(request => request.method !== 'GET' && request.method !== 'OPTIONS')).toHaveLength(0);
});

test('settings storage failure keeps the applied theme and saved value unchanged', async ({ page }) => {
  await mockApi(page);
  await page.goto('/settings');
  const beforeTheme = await page.locator('html').getAttribute('data-theme');
  await page.getByRole('radio', { name: '深色', exact: true }).check();
  await page.evaluate(() => {
    Storage.prototype.setItem = () => { throw new DOMException('Quota exceeded', 'QuotaExceededError'); };
  });
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('无法保存设置');
  expect(await page.locator('html').getAttribute('data-theme')).toBe(beforeTheme);
  expect(await page.evaluate(() => localStorage.getItem('itdesk.preferences.v1'))).toBeNull();
});

test('mobile settings and sidebar are reachable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto('/settings');
  await page.getByRole('tab', { name: '聊天显示', exact: true }).click();
  await expect(page.getByRole('switch', { name: '展开引用原文', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '关于与状态', exact: true }).click();
  await expect(page.getByText(/此处检查本地配置和索引/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.getByRole('button', { name: '展开侧边栏', exact: true }).click();
  await expect(page.getByRole('button', { name: '新会话', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '收起侧边栏', exact: true }).click();
  await expect(page.getByRole('button', { name: '展开侧边栏', exact: true })).toBeVisible();
});
