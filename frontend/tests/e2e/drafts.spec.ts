import { expect, test } from '@playwright/test';
import { DRAFT_ID, mockApi, SESSION_ID } from './mock-api';

test('draft editing stays separate from explicit confirmation and survives refresh', async ({ page }) => {
  const state = await mockApi(page, { history: true, draft: true });
  state.confirmDelayMs = 150;
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('button', { name: '修改草稿', exact: true }).click();
  await page.getByRole('textbox', { name: '草稿标题', exact: true }).fill('邮箱人工核查');
  await page.getByRole('combobox', { name: '问题分类', exact: true }).selectOption('email');
  await page.getByRole('textbox', { name: '问题描述', exact: true }).fill('更正：MAIL-504 同步延迟，未重启。');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByRole('heading', { name: '邮箱人工核查', exact: true })).toBeVisible();
  expect(state.tickets).toHaveLength(1);
  expect(state.sessions[SESSION_ID].drafts[0].status).toBe('pending');
  const edit = state.requests.find(request => request.method === 'PATCH' && request.path === `/api/drafts/${DRAFT_ID}`);
  expect(edit?.body).toEqual({ title: '邮箱人工核查', description: '更正：MAIL-504 同步延迟，未重启。', category: 'email' });
  await page.reload();
  await expect(page.getByRole('heading', { name: '邮箱人工核查', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '确认创建', exact: true }).dblclick();
  await expect(page.getByText('工单 IT-REACT0001 · 已确认', { exact: true })).toBeVisible();
  expect(state.tickets).toHaveLength(2);
  expect(state.tickets[1]).toMatchObject({ title: '邮箱人工核查', category: 'email', priority: 'normal' });
  const confirms = state.requests.filter(request => request.path === `/api/drafts/${DRAFT_ID}/confirm`);
  expect(confirms).toHaveLength(1);
  expect(confirms[0].body).toEqual({});
  await page.reload();
  await expect(page.getByText('工单 IT-REACT0001 · 已确认', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认创建', exact: true })).toHaveCount(0);
});

test('confirmation failure never reports success and requires a persisted-state check before retry', async ({ page }) => {
  const state = await mockApi(page, { draft: true });
  state.failConfirmRequests = 1;
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('button', { name: '确认创建', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('本地存储不可用');
  await expect(page.getByRole('button', { name: '确认创建', exact: true })).toBeDisabled();
  expect(state.tickets).toHaveLength(1);
  expect(state.sessions[SESSION_ID].drafts[0].status).toBe('pending');
  await page.getByRole('button', { name: '刷新草稿状态', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认创建', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '确认创建', exact: true }).click();
  await expect(page.getByText('工单 IT-REACT0001 · 已确认', { exact: true })).toBeVisible();
  expect(state.tickets).toHaveLength(2);
  expect(state.requests.filter(request => request.path === `/api/drafts/${DRAFT_ID}/confirm`)).toHaveLength(2);
});

test('cancelling a persisted draft creates no ticket and remains cancelled after reload', async ({ page }) => {
  const state = await mockApi(page, { draft: true });
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('button', { name: '取消草稿', exact: true }).click();
  await expect(page.getByText('已取消，不会创建工单。', { exact: true })).toBeVisible();
  expect(state.tickets).toHaveLength(1);
  expect(state.sessions[SESSION_ID].drafts[0].status).toBe('cancelled');
  await page.reload();
  await expect(page.getByText('已取消，不会创建工单。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认创建', exact: true })).toHaveCount(0);
});

test('lost PATCH response requires rereading saved content before the old card can be confirmed', async ({ page }) => {
  const state = await mockApi(page, { draft: true });
  state.lostDraftUpdateResponses = 1;
  await page.goto(`/chat?session=${SESSION_ID}`);
  await page.getByRole('button', { name: '修改草稿', exact: true }).click();
  await page.getByRole('textbox', { name: '草稿标题', exact: true }).fill('服务端已保存的邮箱核查');
  await page.getByRole('combobox', { name: '问题分类', exact: true }).selectOption('email');
  await page.getByRole('textbox', { name: '问题描述', exact: true }).fill('MAIL-504 仍同步延迟；已核对重试结果，未修改设置。');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('草稿修改结果尚未确认');
  expect(state.sessions[SESSION_ID].drafts[0].title).toBe('服务端已保存的邮箱核查');
  await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认创建', exact: true })).toBeDisabled();
  expect(state.requests.filter(request => request.path.endsWith('/confirm'))).toHaveLength(0);
  expect(state.tickets).toHaveLength(1);
  await page.getByRole('button', { name: '刷新草稿状态', exact: true }).click();
  await expect(page.getByRole('heading', { name: '服务端已保存的邮箱核查', exact: true })).toBeVisible();
  await expect(page.getByText('MAIL-504 仍同步延迟；已核对重试结果，未修改设置。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认创建', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '确认创建', exact: true }).click();
  await expect(page.getByText('工单 IT-REACT0001 · 已确认', { exact: true })).toBeVisible();
  expect(state.tickets[1]).toMatchObject({ title: '服务端已保存的邮箱核查', category: 'email' });
});
