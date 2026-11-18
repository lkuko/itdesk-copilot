import { expect, test } from '@playwright/test';
import { DRAFT_ID, mockApi, SESSION_ID } from './mock-api';

test('sessions can be created, switched and renamed without submitting a question', async ({ page }) => {
  const state = await mockApi(page, { history: true });
  await page.goto(`/chat?session=${SESSION_ID}`);
  await expect(page.getByRole('textbox', { name: '描述 IT 问题', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '新会话', exact: true }).click();
  await expect(page).toHaveURL(/session=session-new-2$/);
  await expect(page.getByRole('heading', { name: '今天，有什么 IT 问题？', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'VPN 排查记录', exact: false }).first().click();
  await expect(page.getByText('VPN-403 怎么处理？', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '管理会话 VPN 排查记录', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '管理会话', exact: true });
  await dialog.getByRole('textbox', { name: '会话名称', exact: true }).fill('设备登记排查');
  await dialog.getByRole('button', { name: '保存名称', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '设备登记排查', exact: true })).toBeVisible();
  expect(state.sessions[SESSION_ID].title).toBe('设备登记排查');
  expect(state.requests.filter(request => request.path.endsWith('/messages'))).toHaveLength(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: '设备登记排查', exact: true })).toBeVisible();
});

test('deleting a session requires confirmation and preserves already created tickets', async ({ page }) => {
  const state = await mockApi(page, { history: true, draft: true });
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('button', { name: '确认创建', exact: true }).click();
  await expect(page.getByText('工单 IT-REACT0001 · 已确认', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '管理会话 VPN 排查记录', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '管理会话', exact: true });
  await expect(dialog.getByRole('button', { name: '删除会话', exact: true })).toBeDisabled();
  expect(state.requests.filter(request => request.method === 'DELETE')).toHaveLength(0);
  await dialog.getByRole('checkbox', { name: '我确认删除这段会话', exact: true }).check();
  await dialog.getByRole('button', { name: '删除会话', exact: true }).click();
  await expect(page.getByText('会话已删除，已创建工单仍保留。', { exact: true })).toBeVisible();
  expect(state.sessions[SESSION_ID]).toBeUndefined();
  expect(state.tickets).toHaveLength(2);
  expect(state.tickets.find(ticket => ticket.ticket_id === 'IT-REACT0001')?.draft_id).toBeNull();
  expect(state.requests.filter(request => request.path === `/api/drafts/${DRAFT_ID}/confirm`)).toHaveLength(1);
  await page.getByRole('link', { name: '历史工单', exact: true }).click();
  await expect(page.getByRole('article', { name: '工单 IT-REACT0001', exact: true })).toBeVisible();
  await expect(page.getByRole('article', { name: '工单 IT-1001', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('article', { name: '工单 IT-REACT0001', exact: true })).toBeVisible();
});

test('a late refresh of the first page cannot overwrite a more recently selected session page', async ({ page }) => {
  const state = await mockApi(page);
  for (let index = 1; index <= 11; index += 1) {
    const id = `session-paged-${index}`;
    state.sessions[id] = { ...state.sessions[SESSION_ID], session_id: id, title: `分页会话 ${index}`, messages: [], drafts: [] };
  }
  await page.goto('/chat');
  await expect(page.getByRole('button', { name: '较早会话', exact: true })).toBeEnabled();
  let releaseOldPage = () => {};
  let oldPageStarted = false;
  const oldPageGate = new Promise<void>(resolve => { releaseOldPage = resolve; });
  await page.route(/\/api\/sessions\?/, async route => {
    if (new URL(route.request().url()).searchParams.get('offset') === '0') {
      oldPageStarted = true;
      await oldPageGate;
    }
    await route.fallback();
  });
  await page.getByRole('button', { name: '刷新会话', exact: true }).click();
  await expect.poll(() => oldPageStarted).toBe(true);
  await page.getByRole('button', { name: '较早会话', exact: true }).click();
  await expect(page.locator('.session-select').filter({ hasText: '分页会话 10' })).toBeVisible();
  const oldResponse = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname === '/api/sessions' && url.searchParams.get('offset') === '0';
  });
  releaseOldPage();
  await (await oldResponse).finished();
  await page.waitForLoadState('networkidle');
  await expect(page.locator('.session-select').filter({ hasText: '分页会话 10' })).toBeVisible();
  await expect(page.locator('.session-select').filter({ hasText: 'VPN 排查记录' })).toHaveCount(0);
});

test('completion in session A cannot invalidate a delayed session B load after browser Back', async ({ page }) => {
  const state = await mockApi(page, { history: true });
  const sessionB = 'session-mail-0002';
  state.sessions[sessionB] = { ...state.sessions[SESSION_ID], session_id: sessionB, title: '邮箱同步记录', drafts: [],
    messages: state.sessions[SESSION_ID].messages.map(message => ({ ...message, session_id: sessionB,
      content: message.role === 'user' ? '邮箱历史问题' : '邮箱历史回答',
      result: message.role === 'assistant' ? { ...message.result, answer: '邮箱历史回答' } : null })) };
  await page.goto(`/chat?session=${sessionB}`);
  await expect(page.getByText('邮箱历史回答', { exact: true })).toBeVisible();
  await page.locator('.session-select').filter({ hasText: 'VPN 排查记录' }).click();
  await expect(page.getByRole('heading', { name: 'VPN 排查记录', exact: true })).toBeVisible();
  let releaseMessage = () => {};
  let releaseSessionB = () => {};
  let sessionBStarted = false;
  const messageGate = new Promise<void>(resolve => { releaseMessage = resolve; });
  const sessionBGate = new Promise<void>(resolve => { releaseSessionB = resolve; });
  await page.route(`**/api/sessions/${SESSION_ID}/messages`, async route => {
    await messageGate;
    await route.fallback();
  });
  await page.route(`**/api/sessions/${sessionB}`, async route => {
    sessionBStarted = true;
    await sessionBGate;
    await route.fallback();
  });
  await page.getByRole('textbox', { name: '描述 IT 问题', exact: true }).fill('VPN-403 新的问题');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByRole('heading', { name: '正在查阅资料与模拟系统……', exact: true })).toBeVisible();
  await page.goBack();
  await expect.poll(() => sessionBStarted).toBe(true);
  const sentResponse = page.waitForResponse(response => response.url().endsWith(`/api/sessions/${SESSION_ID}/messages`));
  releaseMessage();
  await (await sentResponse).finished();
  await expect(page.getByText('本轮处理完成，结果已保存。', { exact: true })).toBeAttached();
  releaseSessionB();
  await expect(page.getByRole('heading', { name: '邮箱同步记录', exact: true })).toBeVisible();
  await expect(page.getByText('邮箱历史回答', { exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '描述 IT 问题', exact: true })).toBeEnabled();
  await expect(page.getByRole('status').filter({ hasText: '正在恢复会话' })).toHaveCount(0);
});
