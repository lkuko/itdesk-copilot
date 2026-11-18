import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { AgentResult, ConfirmResult } from '../../src/api/types';

const backend = 'http://127.0.0.1:8010';
const workspaceUrl = `${backend}/api/knowledge/workspace`;

test('React knowledge lab reaches the isolated FastAPI and preserves the active index', async ({ page, request }) => {
  const before = await (await request.get(`${backend}/api/health`)).json();
  await page.goto('/knowledge');
  await expect(page.getByRole('heading', { name: '知识库', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^VPN-403 设备登记测试手册/ }).click();
  const preview = page.getByRole('dialog', { name: '原文与切片' });
  await preview.getByText('展开原文', { exact: true }).click();
  await expect(preview.locator('details[open] pre').first()).toContainText('核对设备登记');
  await page.keyboard.press('Escape');
  await page.getByRole('tab', { name: '召回测试' }).click();
  await page.getByText('两种切片策略对比', { exact: true }).click();
  const comparison = page.getByRole('region', { name: '切片策略对比' });
  await comparison.getByLabel('测试查询').fill('VPN-403 如何核对设备登记？');
  await comparison.getByRole('button', { name: '运行对比' }).click();
  await expect(page.getByRole('heading', { name: '优先自然边界' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '固定字符窗口' })).toBeVisible();
  await expect(page.getByText('1.000', { exact: true })).toHaveCount(2);
  const after = await (await request.get(`${backend}/api/health`)).json();
  expect(after.index).toEqual(before.index);
  expect((await request.post(`${backend}/api/bot/telegram`, { data: {} })).status()).toBe(404);
});

async function send(page: Page, question: string): Promise<AgentResult> {
  await page.getByLabel('描述 IT 问题').fill(question);
  const response = page.waitForResponse(res => res.request().method() === 'POST' && res.url().endsWith('/messages'));
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  const result = await response;
  expect(result.status()).toBe(200);
  await expect(page.getByLabel('描述 IT 问题')).toBeEnabled();
  return result.json() as Promise<AgentResult>;
}

test('React → real HTTP → Agent/tools/storage: four flows, replay, edit, confirm and cancel', async ({ page, request }) => {
  test.setTimeout(90_000);
  const consoleErrors: string[] = [];
  page.on('pageerror', error => consoleErrors.push(error.message));
  await page.goto('/chat');
  await expect(page.getByText('服务已就绪', { exact: true })).toBeVisible();

  // A recommendation only fills the composer, without creating a message.
  await page.getByRole('button', { name: 'VPN 连接失败', exact: true }).click();
  await expect(page.getByLabel('描述 IT 问题')).toHaveValue(/VPN-403/);
  await expect(page.getByRole('article', { name: '用户消息' })).toHaveCount(0);

  const answer = await send(page, 'VPN-403 连接失败，如何排查？');
  expect(answer.status).toBe('answered');
  expect(answer.citations?.length).toBeGreaterThan(0);
  expect(answer.tool_trace?.[0]?.name).toBe('search_knowledge');
  await expect(page.getByText('检索到的手册原文', { exact: true })).toBeVisible();
  await page.getByText('01 · VPN-403 设备登记测试手册', { exact: true }).click();
  await expect(page.locator('.source-content')).toContainText('核对设备登记');

  const sessionId = new URL(page.url()).searchParams.get('session');
  expect(sessionId).toBeTruthy();
  const beforeReplay = await (await request.get(`${backend}/api/sessions/${sessionId}`)).json();
  const replay = await request.post(`${backend}/api/sessions/${sessionId}/messages`, {
    data: { question: 'VPN-403 连接失败，如何排查？', request_id: answer.request_id },
  });
  expect(replay.status()).toBe(200);
  expect((await replay.json()).run_id).toBe(answer.run_id);
  const afterReplay = await (await request.get(`${backend}/api/sessions/${sessionId}`)).json();
  expect(afterReplay.messages.length).toBe(beforeReplay.messages.length);

  const status = await send(page, '现在 VPN 服务正常吗？');
  expect(status.tool_trace?.[0]?.name).toBe('get_service_status');
  await expect(page.getByRole('article', { name: '助手消息' }).last()).toContainText('模拟服务状态正常');

  const proposed = await send(page, 'VPN-403 无法连接，请准备人工核查工单草稿，先别提交。');
  expect(proposed.status).toBe('pending_confirmation');
  expect(proposed.draft?.status).toBe('pending');
  const draftId = proposed.draft!.draft_id;
  await page.getByRole('button', { name: '修改草稿', exact: true }).click();
  await page.getByLabel('草稿标题', { exact: true }).fill('已核对设备登记，仍需排查 VPN');
  await page.getByRole('textbox', { name: '问题描述', exact: true }).fill('演示用户陈述：已核对设备登记，VPN-403 仍失败。');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByRole('heading', { name: '已核对设备登记，仍需排查 VPN' })).toBeVisible();
  const saved = await (await request.get(`${backend}/api/drafts/${draftId}`)).json();
  expect(saved.status).toBe('pending');
  expect(saved.title).toBe('已核对设备登记，仍需排查 VPN');

  const confirmation = page.waitForResponse(res => res.url().endsWith(`/drafts/${draftId}/confirm`));
  await page.getByRole('button', { name: '确认创建', exact: true }).click();
  const confirmedResponse = await confirmation;
  expect(confirmedResponse.request().postDataJSON()).toEqual({});
  const confirmed = await confirmedResponse.json() as ConfirmResult;
  await expect(page.getByText(`工单 ${confirmed.ticket.ticket_id} · 已确认`, { exact: true })).toBeVisible();
  const repeated = await request.post(`${backend}/api/drafts/${draftId}/confirm`, { data: {} });
  expect(repeated.status()).toBe(200);
  expect((await repeated.json()).ticket.ticket_id).toBe(confirmed.ticket.ticket_id);
  const tickets = await (await request.get(`${backend}/api/tickets`)).json();
  expect(tickets.tickets.filter((ticket: { draft_id?: string }) => ticket.draft_id === draftId)).toHaveLength(1);

  const queried = await send(page, `帮我查询工单 ${confirmed.ticket.ticket_id} 的处理状态。`);
  expect(queried.tool_trace?.[0]?.name).toBe('get_ticket_status');
  await page.reload();
  await expect(page.getByText(`工单 ${confirmed.ticket.ticket_id} · 已确认`, { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '历史工单', exact: true }).click();
  await expect(page.getByRole('article', { name: `工单 ${confirmed.ticket.ticket_id}`, exact: true })).toContainText('已核对设备登记，仍需排查 VPN');
  await page.getByRole('link', { name: '问答助手', exact: true }).click();

  const cancellable = await send(page, '请再准备一个 VPN 问题草稿，先不要提交。');
  expect(cancellable.draft?.status).toBe('pending');
  await page.getByRole('button', { name: '取消草稿', exact: true }).click();
  await expect(page.getByText('已取消，不会创建工单。', { exact: true })).toBeVisible();
  const forbidden = await request.post(`${backend}/api/drafts/${cancellable.draft!.draft_id}/confirm`, { data: {} });
  expect(forbidden.status()).toBe(409);

  const unrelated = await send(page, '公司是否提供员工住房补贴？');
  expect(unrelated.status).toBe('no_evidence');
  await expect(page.getByText('资料不足，暂时无法给出有依据的结论。', { exact: true })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('document preview and committed workspace profile change the real knowledge results', async ({ page, request }) => {
  const base = await (await request.get(workspaceUrl)).json();
  const profile = {
    ...base.profile,
    chunk_strategy: 'fixed', chunk_size: 120, chunk_overlap: 20,
    search_mode: 'vector', top_k: 2, score_threshold: 0,
    rerank_model: 'local-lexical', rerank_top_n: 2,
  };
  const files = [{
    name: 'vpn-upload.txt', title: 'VPN-905 导入联调手册', category: 'vpn',
    content: 'VPN-905 请打开演示设置，核对备用设备登记。',
  }];
  const preview = await request.post(`${workspaceUrl}/preview`, { data: { files, profile } });
  expect(preview.ok()).toBeTruthy();
  const body = await preview.json();
  expect(body.documents[0].chunks[0].content).toBe(files[0].content);
  expect((await (await request.get(workspaceUrl)).json()).revision).toBe(base.revision);

  const commit = await request.post(`${workspaceUrl}/apply`, {
    data: { files, profile, revision: base.revision, remove_ids: [] },
  });
  expect(commit.ok()).toBeTruthy();
  const committed = await commit.json();
  expect(committed.rebuilt).toBe(true);
  const importedID = committed.managed_ids[0];
  expect(importedID).toBeTruthy();
  expect(committed.profile).toEqual(profile);

  await page.goto('/knowledge');
  await expect(page.getByText('VPN-905 导入联调手册', { exact: true }).first()).toBeVisible();
  const search = await request.post(`${workspaceUrl}/search`, { data: { query: 'VPN-905 备用设备登记' } });
  expect(search.ok()).toBeTruthy();
  const results = await search.json();
  expect(results.rerank_status).toBe('applied');
  expect(results.hits[0].doc_id).toBe(importedID);
  expect(typeof results.hits[0].rerank_score).toBe('number');

  const conflict = await request.post(`${workspaceUrl}/apply`, {
    data: { profile, files: [], revision: base.revision, remove_ids: [importedID] },
  });
  expect(conflict.status()).toBe(409);
  const rankOnly = await request.post(`${workspaceUrl}/apply`, {
    data: { profile: { ...profile, rerank_model: null }, files: [], revision: committed.revision, remove_ids: [] },
  });
  expect(rankOnly.ok()).toBeTruthy();
  expect((await rankOnly.json()).rebuilt).toBe(false);
});
