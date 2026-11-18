import { expect, test, type Page } from '@playwright/test';
import type { ImportFile, Profile, Workspace } from '../../src/features/knowledge/workspaceApi';
import { mockApi } from './mock-api';

const vpn = {
  doc_id: 'VPN-403', title: 'VPN 设备登记', source: 'vpn/device-registration.md', version: '2.1',
};
const email = {
  doc_id: 'EMAIL-101', title: '邮箱重复验证', source: 'email/repeated-login.md', version: '1.0',
};
const vpnFirst = '先核对模拟设备登记状态。';
const vpnSecond = '授权变更由人工核实。';
const documents = [
  { ...vpn, content: `${vpnFirst}\n${vpnSecond}`, chunks: [vpnFirst, vpnSecond].map((content, index) => {
    const start = index === 0 ? 0 : vpnFirst.length + 1;
    return { chunk_id: `${vpn.doc_id}-${index + 1}`, content, start, end: start + content.length };
  }) },
  { ...email, content: '核对客户端版本。', chunks: [
    { chunk_id: `${email.doc_id}-1`, content: '核对客户端版本。', start: 0, end: '核对客户端版本。'.length },
  ] },
];
const settings = { embedding_model: 'mock-embedding', chunk_size: 320, chunk_overlap: 48 };
const initialProfile: Profile = {
  ...settings, chunk_strategy: 'boundary', search_mode: 'exact_vector', top_k: 5,
  score_threshold: 0.5, rerank_model: null, rerank_top_n: 10,
};
const importText = '---\ndoc_id: mock-import\ntitle: 网络自检\ncategory: vpn\nversion: "1.0"\n---\n检查模拟网络状态。';
const imported = {
  doc_id: 'mock-import', title: '网络自检', source: 'check-network.md', version: '1.0', content: '检查模拟网络状态。',
  chunks: [{ chunk_id: 'mock-import-1', content: '检查模拟网络状态。', start: 0, end: 9 }],
};
type ApplyRequest = { profile: Profile; revision: string; files: ImportFile[]; remove_ids: string[] };

async function mockKnowledge(page: Page) {
  const api = await mockApi(page);
  const comparisons: unknown[] = [];
  const unexpected: string[] = [];
  const previews: unknown[] = [];
  const applies: ApplyRequest[] = [];
  const searches: string[] = [];
  let currentDocuments = [...documents];
  const workspace: Workspace = {
    profile: { ...initialProfile }, embedding_models: ['mock-embedding', 'mock-alternate'],
    rerank_models: ['local-lexical'], revision: 'original', managed_ids: [],
    index: { status: 'ready', message: '问答索引可用' },
  };
  await page.route(/\/api\/knowledge(?:\/|$)/, async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const json = (payload: unknown, status = 200) => route.fulfill({
      status, json: payload,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
        'Access-Control-Allow-Headers': 'content-type',
      },
    });
    if (method === 'OPTIONS') return json({});
    if (method === 'GET' && path === '/api/knowledge') {
      return json({ ...settings, index: { status: 'ready', message: '问答索引可用' }, documents: currentDocuments
        .map(({ doc_id, title, source, version, chunks }) => ({ doc_id, title, source, version, chunk_count: chunks.length })) });
    }
    if (method === 'GET' && path === '/api/knowledge/workspace') return json(workspace);
    if (method === 'GET') {
      const document = currentDocuments.find(item => path === `/api/knowledge/documents/${item.doc_id}`);
      if (document) return json(document);
    }
    if (method === 'POST' && path === '/api/knowledge/workspace/preview') {
      previews.push(request.postDataJSON());
      return json({ documents: [imported] });
    }
    if (method === 'POST' && path === '/api/knowledge/workspace/apply') {
      const body = request.postDataJSON() as ApplyRequest;
      applies.push(body);
      if (body.revision !== workspace.revision) {
        return json({ detail: { code: 'workspace_conflict', message: '知识库已更新。' } }, 409);
      }
      currentDocuments = currentDocuments.filter(document => !body.remove_ids.includes(document.doc_id));
      workspace.managed_ids = workspace.managed_ids.filter(id => !body.remove_ids.includes(id));
      if (body.files.length) { currentDocuments.push(imported); workspace.managed_ids.push(imported.doc_id); }
      workspace.profile = body.profile;
      workspace.revision = `revision-${applies.length}`;
      return json(workspace);
    }
    if (method === 'POST' && path === '/api/knowledge/workspace/search') {
      const { query } = request.postDataJSON() as { query: string };
      searches.push(query);
      return json({
        hits: workspace.managed_ids.includes(imported.doc_id) ? [{
          ...imported, chunk_id: imported.chunks[0].chunk_id, similarity: 0.87654, rerank_score: null, exact_match: false,
        }] : [], rerank_status: workspace.profile.rerank_model ? 'fallback' : 'disabled', embedding_tokens: 8,
      });
    }
    if (method === 'POST' && path === '/api/knowledge/compare') {
      const body = request.postDataJSON() as { query: string; top_k: number };
      comparisons.push(body);
      const fixedExcerpt = `${vpnFirst}\n${vpnSecond}`;
      return json({ ...settings, query: body.query, top_k: body.top_k, unique_chunk_inputs: 4,
        document_embedding_tokens: null, query_embedding_tokens: 8,
        strategies: [
          { strategy: 'boundary', chunk_count: 3, embedding_characters: 65, hits: [
            { ...vpn, ...documents[0].chunks[0], similarity: 0.87654 },
          ] },
          { strategy: 'fixed', chunk_count: 2, embedding_characters: 55, hits: [
            { ...vpn, chunk_id: 'VPN-403-fixed', content: fixedExcerpt, start: 0, end: fixedExcerpt.length, similarity: -0.125 },
          ] },
        ],
      });
    }
    unexpected.push(`${method} ${path}`);
    return json({ detail: { code: 'mock_route_missing', message: '未定义知识库测试接口' } }, 404);
  });
  return { api, comparisons, unexpected, previews, applies, searches };
}

test('finds a manual by source and previews its original text and selected chunk', async ({ page }) => {
  const { api, comparisons, unexpected } = await mockKnowledge(page);
  await page.goto('/knowledge');
  await expect(page.getByRole('heading', { name: '知识库', exact: true })).toBeVisible();
  const manuals = page.getByRole('region', { name: '文档列表', exact: true });
  const preview = page.getByRole('dialog', { name: '原文与切片' });
  const search = manuals.getByRole('searchbox', { name: '搜索标题、编号或文件' });
  const emailItem = manuals.locator('details').filter({ hasText: email.title });
  const vpnItem = manuals.locator('details').filter({ hasText: vpn.title });
  await expect(emailItem).toBeVisible();
  await expect(emailItem).not.toHaveAttribute('open');

  await search.fill('NO_MATCH');
  await expect(manuals.getByText('没有匹配的手册。')).toBeVisible();
  await search.fill('DEVICE-REGISTRATION.MD');
  await expect(emailItem).toHaveCount(0);
  await vpnItem.locator('summary').click();
  await expect(vpnItem).toHaveAttribute('open', '');
  await expect(vpnItem.getByText(`${vpn.source}`, { exact: true })).toBeVisible();
  await expect(emailItem).toHaveCount(0);
  await vpnItem.getByRole('button', { name: '查看原文与切片' }).click();
  await expect(preview.getByRole('heading', { name: vpn.title })).toBeVisible();
  await expect(preview.getByText(`${vpn.source} · ${vpn.version}`)).toBeVisible();

  await preview.getByText('展开原文', { exact: true }).click();
  await expect(preview.locator('details[open] pre')).toHaveText(`${vpnFirst}\n${vpnSecond}`);
  const secondChunk = documents[0].chunks[1];
  const chunk = preview.locator('details').filter({ has: page.getByText(
    `片段 2 · [${secondChunk.start}, ${secondChunk.end})`, { exact: true },
  ) });
  await chunk.locator('summary').click();
  await expect(chunk.locator('pre')).toHaveText(vpnSecond);
  expect(comparisons).toEqual([]);
  expect(unexpected).toEqual([]);
  expect(api.requests.filter(request => request.method !== 'OPTIONS').every(request =>
    request.method === 'GET' && ['/api/health', '/api/sessions'].includes(request.path))).toBe(true);
});

test('compares two strategies on mobile and displays scores without sending a chat request', async ({ page }) => {
  const { api, comparisons, unexpected } = await mockKnowledge(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/knowledge');
  await page.getByRole('tab', { name: '召回测试' }).click();
  await page.getByText('两种切片策略对比', { exact: true }).click();
  const compare = page.getByRole('region', { name: '切片策略对比' });
  const run = compare.getByRole('button', { name: '运行对比' });
  await expect(run).toBeDisabled();
  await compare.getByRole('textbox', { name: '测试查询' }).fill('  VPN 反复验证失败  ');
  await compare.getByRole('combobox', { name: 'Top K' }).selectOption('1');
  await run.click();

  await expect(compare.getByRole('status')).toContainText('“VPN 反复验证失败” · Top 1 · mock-embedding');
  await expect(compare.getByText('去重后 4 个切片输入 · 文档 未知 Token · 查询 8 Token')).toBeVisible();
  const boundary = compare.locator('section').filter({ has: page.getByRole('heading', { name: '优先自然边界' }) });
  const fixed = compare.locator('section').filter({ has: page.getByRole('heading', { name: '固定字符窗口' }) });
  await expect(boundary.getByText('0.877', { exact: true })).toBeVisible();
  await expect(fixed.getByText('-0.125', { exact: true })).toBeVisible();
  await expect(boundary.getByRole('meter', { name: '余弦相似度' })).toHaveAttribute('max', '1');
  await expect(fixed.getByRole('meter', { name: '余弦相似度' })).toHaveAttribute('min', '-1');
  await fixed.getByText('核对片段内容', { exact: true }).click();
  await expect(fixed.locator('pre')).toHaveText(`${vpnFirst}\n${vpnSecond}`);

  const bounds = await boundary.boundingBox();
  const fixedBounds = await fixed.boundingBox();
  expect(bounds).not.toBeNull();
  expect(fixedBounds).not.toBeNull();
  if (!bounds || !fixedBounds) throw new Error('Expected both strategy panels to have layout bounds.');
  expect(fixedBounds.y).toBeGreaterThanOrEqual(bounds.y + bounds.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(comparisons).toEqual([{ query: 'VPN 反复验证失败', top_k: 1 }]);
  expect(unexpected).toEqual([]);
  expect(api.requests.filter(request => request.method !== 'OPTIONS').every(request =>
    request.method === 'GET' && ['/api/health', '/api/sessions'].includes(request.path))).toBe(true);
});

test('previews a UTF-8 import, applies it, retrieves its chunk, and removes it by confirmation', async ({ page }) => {
  const { api, previews, applies, searches, unexpected } = await mockKnowledge(page);
  await page.goto('/knowledge');
  await page.getByRole('button', { name: '导入文档' }).click();
  const wizard = page.getByRole('dialog', { name: '导入文档' });
  await wizard.getByLabel('选择文档').setInputFiles({
    name: imported.source, mimeType: 'text/markdown', buffer: Buffer.from(importText),
  });
  await wizard.getByRole('button', { name: '预览切片', exact: true }).click();
  await expect(wizard.getByRole('heading', { name: imported.title })).toBeVisible();
  const files = [{ name: imported.source, content: importText }];
  expect(previews).toEqual([{ profile: initialProfile, files }]);
  expect(applies).toEqual([]);
  await wizard.getByRole('button', { name: '确认并建立索引' }).click();
  await expect(wizard).toHaveCount(0);
  const importedItem = page.locator('details').filter({ hasText: imported.title });
  await expect(importedItem).toContainText('已导入');
  expect(applies).toEqual([{ profile: initialProfile, files, remove_ids: [], revision: 'original' }]);

  await page.getByRole('tab', { name: '召回测试' }).click();
  const recall = page.getByRole('region', { name: '召回测试', exact: true });
  await recall.getByRole('textbox', { name: '测试查询' }).fill('  网络状态  ');
  await recall.getByRole('button', { name: '运行召回' }).click();
  await expect(recall.getByRole('status')).toContainText('1 条结果 · 未启用重排');
  await expect(recall.getByText(imported.content, { exact: true })).toBeVisible();
  await expect(recall.getByText('0.877', { exact: true })).toBeVisible();
  expect(searches).toEqual(['网络状态']);

  await page.getByRole('tab', { name: '文档', exact: true }).click();
  await expect(page.getByRole('button', { name: `移除 ${vpn.title}` })).toHaveCount(0);
  await importedItem.locator('summary').click();
  await importedItem.getByRole('button', { name: `移除 ${imported.title}` }).click();
  const remove = page.getByRole('dialog', { name: '移除文档', exact: true });
  expect(applies).toHaveLength(1);
  await remove.getByRole('button', { name: '确认移除' }).click();
  await expect(importedItem).toHaveCount(0);
  expect(applies[1]).toEqual({ profile: initialProfile, files: [], remove_ids: [imported.doc_id], revision: 'revision-1' });
  expect(unexpected).toEqual([]);
  expect(api.requests.filter(request => request.path.endsWith('/messages'))).toEqual([]);
});

test('applies only confirmed model and retrieval settings and exposes rerank fallback in recall', async ({ page }) => {
  const { applies } = await mockKnowledge(page);
  await page.goto('/knowledge');
  await page.getByRole('tab', { name: '设置', exact: true }).click();
  const panel = page.getByRole('region', { name: '知识库设置' });
  await panel.getByRole('combobox', { name: 'Embedding 模型' }).selectOption('mock-alternate');
  await panel.getByRole('combobox', { name: '切片策略' }).selectOption('fixed');
  await panel.getByRole('combobox', { name: '检索模式' }).selectOption('vector');
  await panel.getByRole('combobox', { name: '重排模型' }).selectOption('local-lexical');
  await panel.getByRole('spinbutton', { name: 'Top K' }).fill('2');
  await panel.getByRole('spinbutton', { name: '重排候选数' }).fill('4');
  await panel.getByRole('button', { name: '应用设置' }).click();
  const review = page.getByRole('dialog', { name: '应用知识库设置' });
  await expect(review.getByText(/应用时将重建索引/)).toBeVisible();
  expect(applies).toEqual([]);
  await review.getByRole('button', { name: '确认应用' }).click();
  await expect(review).toHaveCount(0);
  expect(applies[0]).toEqual({ profile: {
    ...initialProfile, embedding_model: 'mock-alternate', chunk_strategy: 'fixed', search_mode: 'vector',
    rerank_model: 'local-lexical', top_k: 2, rerank_top_n: 4,
  }, revision: 'original', files: [], remove_ids: [] });
  await page.getByRole('tab', { name: '召回测试' }).click();
  const recall = page.getByRole('region', { name: '召回测试', exact: true });
  await expect(recall.getByText(/仅向量 · Top 2/)).toBeVisible();
  await recall.getByRole('textbox', { name: '测试查询' }).fill('网络状态');
  await recall.getByRole('button', { name: '运行召回' }).click();
  await expect(recall.getByRole('status')).toContainText('重排不可用，已保留原排序');
});
