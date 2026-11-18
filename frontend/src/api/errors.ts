import { redactSensitiveText } from './safety';

export class ApiError extends Error {
  constructor(public code: string, message: string, public status?: number) {
    super(redactSensitiveText(message)); this.name = 'ApiError';
  }
}
export function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作未完成，请稍后重试。';
}
export function responseError(status: number, payload: unknown): ApiError {
  const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : undefined;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const value = detail as { code?: unknown; message?: unknown };
    if (typeof value.code === 'string' && typeof value.message === 'string') return new ApiError(value.code, value.message, status);
  }
  if (Array.isArray(detail)) {
    const messages = detail.slice(0, 3).flatMap(item => item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string' ? [redactSensitiveText(item.msg)] : []);
    if (messages.length) return new ApiError(`http_${status}`, messages.join('；'), status);
  }
  const messages: Record<number, string> = { 422: '输入参数不合法，请检查内容后重试。', 404: '记录不存在或当前用户不可查看。', 409: '当前状态发生冲突，请刷新会话后重试。', 503: '本地存储或服务暂不可用，请检查后重试。' };
  return new ApiError(`http_${status}`, messages[status] ?? `请求失败（HTTP ${status}），请稍后重试。`, status);
}
