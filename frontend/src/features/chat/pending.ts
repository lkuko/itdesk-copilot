import { sensitiveInputReason } from '../../api/safety';

export interface PendingRequest { session_id: string; request_id: string; question: string; started_at: string }
export const PENDING_KEY = 'itdesk.pending.v1';
function isPending(value: unknown): value is PendingRequest {
  if (!value || typeof value !== 'object') return false;
  const v = value as Partial<PendingRequest>;
  return typeof v.session_id === 'string' && v.session_id.length > 0 &&
    typeof v.request_id === 'string' && /^[a-zA-Z0-9_-]{8,64}$/.test(v.request_id) &&
    typeof v.question === 'string' && v.question.trim().length > 0 && v.question.length <= 2000 &&
    typeof v.started_at === 'string' && Number.isFinite(Date.parse(v.started_at)) && !sensitiveInputReason(v.question);
}
export function loadPending(storage?: Storage): PendingRequest | null {
  try {
    const value: unknown = JSON.parse((storage ?? window.sessionStorage).getItem(PENDING_KEY) ?? 'null');
    return isPending(value) ? value : null;
  } catch { return null; }
}
export function savePending(value: PendingRequest, storage?: Storage): boolean {
  if (!isPending(value)) return false;
  try { (storage ?? window.sessionStorage).setItem(PENDING_KEY, JSON.stringify(value)); return true; }
  catch { return false; }
}
export function clearPending(storage?: Storage): void {
  try { (storage ?? window.sessionStorage).removeItem(PENDING_KEY); } catch { /* In-memory state can still be cleared. */ }
}
