import { describe, expect, it } from 'vitest';
import { clearPending, loadPending, PENDING_KEY, savePending } from '../src/features/chat/pending';

const request = { session_id: 'session-00000001', request_id: 'request-persist-0001',
  question: 'VPN-403 设备未授权，怎么核对？', started_at: '2026-11-18T04:00:00Z' };

describe('pending request recovery', () => {
  it('restores the original request ID, question and session after remount', () => {
    expect(savePending(request)).toBe(true);
    expect(loadPending()).toEqual(request);
    expect(localStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('clears only the pending request key', () => {
    sessionStorage.setItem('other-tab-state', 'keep');
    savePending(request);
    clearPending();
    expect(loadPending()).toBeNull();
    expect(sessionStorage.getItem('other-tab-state')).toBe('keep');
  });

  it.each([
    '{broken', 'null', '[]',
    JSON.stringify({ ...request, request_id: 'invalid id' }),
    JSON.stringify({ ...request, question: '   ' }),
    JSON.stringify({ ...request, started_at: 'not a date' }),
  ])('rejects corrupt or invalid persisted recovery data', (raw) => {
    sessionStorage.setItem(PENDING_KEY, raw);
    expect(loadPending()).toBeNull();
  });

  it('does not persist recognized credentials as a pending question', () => {
    expect(savePending({ ...request, question: 'password=OnlyATestCredential42' })).toBe(false);
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('keeps the chat recoverable in memory when session storage is unavailable', () => {
    const unavailable = {
      getItem() { throw new DOMException('Denied', 'SecurityError'); },
      setItem() { throw new DOMException('Denied', 'SecurityError'); },
      removeItem() { throw new DOMException('Denied', 'SecurityError'); },
    } as unknown as Storage;
    expect(savePending(request, unavailable)).toBe(false);
    expect(loadPending(unavailable)).toBeNull();
    expect(() => clearPending(unavailable)).not.toThrow();
  });
});
