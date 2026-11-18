import { expect, test } from '@playwright/test';
import { mockApi, SESSION_ID } from './mock-api';

test('a submitted question restores the persisted answer with citations and read-only tool records', async ({ page }) => {
  const state = await mockApi(page);
  await page.goto(`/chat?session=${SESSION_ID}`);
  const input = page.getByRole('textbox', { name: '描述 IT 问题', exact: true });
  await expect(input).toBeEnabled();
  await input.fill('VPN-403 设备未授权应该怎么排查？');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByText('请先核对模拟设备登记状态；需要变更设备授权时交由人工核实。', { exact: true })).toBeVisible();
  await page.getByText('01 · VPN-403：设备登记检查', { exact: true }).click();
  await expect(page.getByText('设备授权由人工核实。助手不能开通授权或修改白名单。', { exact: true })).toBeVisible();
  await page.getByText('只读工具与运行记录 · 1 次请求', { exact: true }).click();
  await expect(page.getByText('01 · 检索手册 · 完成', { exact: true })).toBeVisible();
  const sent = state.requests.filter(request => request.path.endsWith('/messages'));
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toMatchObject({ question: 'VPN-403 设备未授权应该怎么排查？' });
  expect(sent[0].body?.request_id).toEqual(expect.any(String));
  expect(state.tickets).toHaveLength(1);
  await page.reload();
  await expect(page.getByText('请先核对模拟设备登记状态；需要变更设备授权时交由人工核实。', { exact: true })).toBeVisible();
  expect(state.requests.filter(request => request.path.endsWith('/messages'))).toHaveLength(1);
});

test('composer supports multiline input and IME composition without accidental sends', async ({ page }) => {
  const state = await mockApi(page);
  await page.goto(`/chat?session=${SESSION_ID}`);
  const input = page.getByRole('textbox', { name: '描述 IT 问题', exact: true });
  const send = page.getByRole('button', { name: '发送问题', exact: true });
  await expect(input).toBeEnabled();
  await input.fill('   ');
  await expect(send).toBeDisabled();
  await input.fill('第一行');
  await input.press('Shift+Enter');
  await input.pressSequentially('第二行');
  await expect(input).toHaveValue('第一行\n第二行');
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
  expect(state.requests.filter(request => request.path.endsWith('/messages'))).toHaveLength(0);
  await expect(input).toHaveAttribute('maxlength', '2000');
  await input.fill('VPN-403 无法连接');
  await input.press('Enter');
  await expect(page.getByRole('article', { name: '助手消息' })).toBeVisible();
  expect(state.requests.filter(request => request.path.endsWith('/messages'))).toHaveLength(1);
});

test('recognized credentials are blocked before any business request', async ({ page }) => {
  const state = await mockApi(page);
  await page.goto('/chat');
  await page.getByRole('textbox', { name: '描述 IT 问题', exact: true }).fill('VPN 无法登录，password=demo-secret');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('请先移除密码、密钥或认证信息');
  expect(state.requests.filter(request => request.method !== 'GET' && request.method !== 'OPTIONS')).toHaveLength(0);
  expect(await page.evaluate(() => sessionStorage.getItem('itdesk.pending.v1'))).toBeNull();
});

test('network failure exposes an explicit retry that reuses the original request ID', async ({ page }) => {
  const state = await mockApi(page);
  state.failMessageRequests = 1;
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('textbox', { name: '描述 IT 问题', exact: true }).fill('VPN-403 设备未授权');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByRole('heading', { name: '原请求结果待确认', exact: true })).toBeVisible();
  let sent = state.requests.filter(request => request.path.endsWith('/messages'));
  expect(sent).toHaveLength(1);
  const originalId = sent[0].body?.request_id;
  await page.getByRole('button', { name: '重试原请求', exact: true }).click();
  await expect(page.getByRole('article', { name: '助手消息' })).toBeVisible();
  sent = state.requests.filter(request => request.path.endsWith('/messages'));
  expect(sent).toHaveLength(2);
  expect(sent[1].body?.request_id).toBe(originalId);
  expect(state.sessions[SESSION_ID].messages).toHaveLength(2);
  expect(await page.evaluate(() => sessionStorage.getItem('itdesk.pending.v1'))).toBeNull();
});

test('refresh recovers a result committed before a network failure without a second POST', async ({ page }) => {
  const state = await mockApi(page);
  state.failMessageRequests = 1;
  state.commitBeforeNetworkFailure = true;
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('textbox', { name: '描述 IT 问题', exact: true }).fill('VPN-403 设备未授权');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByRole('heading', { name: '原请求结果待确认', exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('itdesk.pending.v1'))).not.toBeNull();
  await page.reload();
  await expect(page.getByText('原请求已完成，已恢复保存的结果。', { exact: true })).toBeVisible();
  await expect(page.getByRole('article', { name: '助手消息' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '描述 IT 问题', exact: true })).toBeEnabled();
  expect(state.requests.filter(request => request.path.endsWith('/messages'))).toHaveLength(1);
  expect(await page.evaluate(() => sessionStorage.getItem('itdesk.pending.v1'))).toBeNull();
});

test('no-evidence response explains the boundary and preparing a handoff only prefills input', async ({ page }) => {
  const state = await mockApi(page);
  state.replyStatus = 'no_evidence';
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('textbox', { name: '描述 IT 问题', exact: true }).fill('VPN 没有错误码但无法使用');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByText('资料不足，暂时无法给出有依据的结论。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '准备人工核查草稿', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '描述 IT 问题', exact: true })).toHaveValue('VPN 没有错误码但无法使用\n请为这个问题准备人工核查工单草稿，先别提交。');
  expect(state.requests.filter(request => request.path.endsWith('/messages'))).toHaveLength(1);
  expect(state.sessions[SESSION_ID].drafts).toHaveLength(0);
  expect(state.tickets).toHaveLength(1);
});

test('model failure remains a failed answer and retry only prefills the original question', async ({ page }) => {
  const state = await mockApi(page);
  state.replyStatus = 'failed';
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('textbox', { name: '描述 IT 问题', exact: true }).fill('VPN-403 怎么办？');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('模型请求超时');
  await page.getByRole('button', { name: '重新提问', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '描述 IT 问题', exact: true })).toHaveValue('VPN-403 怎么办？');
  expect(state.requests.filter(request => request.path.endsWith('/messages'))).toHaveLength(1);
});

for (const code of ['request_interrupted', 'request_id_conflict']) {
  test(`${code} requires explicit new-ID submission and cannot reuse an unrelated old answer`, async ({ page }) => {
    const state = await mockApi(page, { history: true });
    const bodies: Record<string, unknown>[] = [];
    await page.route(`**/api/sessions/${SESSION_ID}/messages`, async route => {
      if (route.request().method() !== 'POST') { await route.fallback(); return; }
      const body = route.request().postDataJSON() as Record<string, unknown>;
      bodies.push(body);
      if (bodies.length === 1) {
        if (code === 'request_id_conflict') {
          for (const message of state.sessions[SESSION_ID].messages) {
            message.request_id = String(body.request_id);
            if (message.role === 'user') message.content = '不同的历史问题';
            else if (message.result) message.result.request_id = body.request_id;
          }
        }
        await route.fulfill({ status: 409, contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': '*' }, body: JSON.stringify({
          detail: { code, message: code === 'request_interrupted' ? '原请求已中断。' : '请求编号已用于其他问题。' },
        }) });
        return;
      }
      await route.fallback();
    });
    await page.goto(`/chat?session=${SESSION_ID}`);
    await page.getByRole('textbox', { name: '描述 IT 问题', exact: true }).fill('当前的新问题 VPN-403');
    await page.getByRole('button', { name: '发送问题', exact: true }).click();
    await expect(page.getByRole('button', { name: '以新请求重新提交', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '重试原请求', exact: true })).toHaveCount(0);
    expect(bodies).toHaveLength(1);
    await page.locator('.pending-panel').getByRole('button', { name: '刷新会话', exact: true }).click();
    await expect(page.getByRole('heading', { name: '原请求结果待确认', exact: true })).toBeVisible();
    await expect(page.getByRole('textbox', { name: '描述 IT 问题', exact: true })).toBeDisabled();
    expect(await page.evaluate(() => sessionStorage.getItem('itdesk.pending.v1'))).not.toBeNull();
    await page.getByRole('button', { name: '以新请求重新提交', exact: true }).click();
    await expect(page.getByRole('textbox', { name: '描述 IT 问题', exact: true })).toBeEnabled();
    expect(bodies).toHaveLength(2);
    expect(bodies[1].request_id).not.toBe(bodies[0].request_id);
    expect(bodies[1].question).toBe('当前的新问题 VPN-403');
    expect(await page.evaluate(() => sessionStorage.getItem('itdesk.pending.v1'))).toBeNull();
  });
}
