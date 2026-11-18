// Limited convenience masking for demo records; the backend enforces input safety.
const TOKEN = /\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,})|-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----/i;
const LABELED = /((?:API[ _-]?KEY|access[ _-]?token|refresh[ _-]?token|client[ _-]?secret|password|passwd|token|secret|密码|口令|密钥|令牌|验证码|恢复码)\s*(?:是|为|[:：=])\s*["'`]?)([^\s，。；,;"'`]+)/gi;
const BENIGN = /^(?:错误|不正确|忘记|未知|未设置|未配置|已遮蔽|已隐藏|已删除|incorrect|invalid|unknown|missing|redacted|hidden|none|null|false|true)$/i;
const CREDENTIAL_FIELD = /^(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|authorization|password|passwd|token|secret|密码|口令|密钥|令牌|验证码|恢复码)$/i;
const candidateValue = (value: string) => value.replace(/^[[【<《]+|[\]】>》]+$/g, '');

export function redactSensitiveText(value: string): string {
  const withoutPrivateKeys = value.replace(/-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----[\s\S]*?(?:-----END(?: [A-Z]+)? PRIVATE KEY-----|$)/g, '[已遮蔽的私钥]');
  const withoutLabeled = withoutPrivateKeys.replace(LABELED, (whole: string, prefix: string, value: string) => {
    const candidate = candidateValue(value);
    return !candidate || BENIGN.test(candidate) ? whole : `${prefix}[已遮蔽]`;
  });
  return withoutLabeled.replace(new RegExp(TOKEN.source, 'gi'), '[已遮蔽]');
}
export function redactSensitiveValue<T>(value: T): T {
  if (typeof value === 'string') return redactSensitiveText(value) as T;
  if (Array.isArray(value)) return value.map(redactSensitiveValue) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [redactSensitiveText(key),
      CREDENTIAL_FIELD.test(key) && child ? '[已遮蔽]' : redactSensitiveValue(child),
    ])) as T;
  }
  return value;
}
export function sensitiveInputReason(text: string): string | null {
  return redactSensitiveText(text) !== text ? '请先移除密码、密钥或认证信息。这里只接受模拟问题。' : null;
}
