import { describe, expect, it, vi } from 'vitest';
import { fetchJson } from '../src/api/client';
import { ApiError, errorText } from '../src/api/errors';

function respond(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('REST client transport and error boundaries', () => {
  it('sends one JSON mutation with its request identifier', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond({ status: 'answered' }));
    vi.stubGlobal('fetch', fetchMock);
    const body = { question: 'VPN-403 怎么处理？', request_id: 'request-12345678' };
    await expect(fetchJson('/api/sessions/demo/messages', { method: 'POST', body }))
      .resolves.toEqual({ status: 'answered' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/sessions/demo/messages');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual(body);
  });

  it.each([
    [404, 'session_not_found', '会话不存在。'],
    [409, 'session_busy', '会话正在处理，请稍后重试。'],
    [503, 'storage_unavailable', '本地存储不可用。'],
    [422, 'sensitive_input', '请勿输入密码或密钥。'],
  ])('keeps structured HTTP %s errors actionable', async (status, code, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond({ detail: { code, message } }, status)));
    const caught = await fetchJson('/api/sessions/demo').catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ code, status });
    expect(errorText(caught)).toContain(message);
  });

  it('maps Pydantic validation errors without dumping request fields', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond({ detail: [
      { loc: ['body', 'question'], msg: '问题不能为空', type: 'value_error' },
    ] }, 422)));
    const caught = await fetchJson('/api/sessions/demo/messages', {
      method: 'POST', body: { question: ' ', request_id: 'request-12345678' },
    }).catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(ApiError);
    expect(errorText(caught)).toContain('问题不能为空');
    expect(errorText(caught)).not.toContain('"loc"');
  });

  it('rejects an HTML or malformed JSON response without rendering its body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>private-upstream-body</html>')));
    const caught = await fetchJson('/api/health').catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(ApiError);
    expect(errorText(caught)).not.toContain('private-upstream-body');
  });

  it('reports a network failure and never automatically repeats a mutation', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    const caught = await fetchJson('/api/sessions', { method: 'POST', body: {} })
      .catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(ApiError);
    expect(errorText(caught).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('aborts a timed out request, preserving the distinction from a completed answer', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    vi.stubGlobal('fetch', fetchMock);
    const result = fetchJson('/api/sessions/demo/messages', {
      method: 'POST', body: { question: 'VPN', request_id: 'request-timeout' }, timeoutMs: 20,
    }).catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(25);
    const caught = await result;
    expect(caught).toBeInstanceOf(ApiError);
    expect(errorText(caught)).toContain('超时');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
