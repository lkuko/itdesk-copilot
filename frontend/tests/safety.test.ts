import { describe, expect, it } from 'vitest';
import { redactSensitiveText, redactSensitiveValue, sensitiveInputReason } from '../src/api/safety';
import { ApiError } from '../src/api/errors';

describe('limited credential checks for demo input and display', () => {
  it('allows ordinary password error reports rather than blocking the topic', () => {
    for (const text of ['VPN 提示密码：错误', '邮箱显示密码：不正确', 'password=incorrect', '访问令牌：已隐藏']) {
      expect(sensitiveInputReason(text)).toBeNull();
      expect(redactSensitiveText(text)).toBe(text);
    }
  });

  it.each([
    ['OpenAI-style token', 'sk-onlyatesttoken123', 'sk-onlyatesttoken123'],
    ['GitHub-style token', 'ghp_onlyatesttoken123456', 'ghp_onlyatesttoken123456'],
    ['Bearer token', 'Authorization: Bearer OnlyATestToken.1234', 'OnlyATestToken.1234'],
    ['Chinese password label', 'VPN 密码：OnlyATestPassword42', 'OnlyATestPassword42'],
    ['Chinese recovery label', '恢复码为DemoRecovery123', 'DemoRecovery123'],
    ['unfinished private key', '-----BEGIN RSA PRIVATE KEY-----\nDemoPrivateKeyPayload', 'DemoPrivateKeyPayload'],
    ['finished private key', '-----BEGIN PRIVATE KEY-----\nDemoPrivateKeyPayload\n-----END PRIVATE KEY-----', 'DemoPrivateKeyPayload'],
  ])('blocks and masks a recognized %s without echoing it in the explanation', (_name, text, secret) => {
    const explanation = sensitiveInputReason(text);
    expect(explanation).toContain('请先移除');
    expect(explanation).not.toContain(secret);
    expect(redactSensitiveText(text)).not.toContain(secret);
  });

  it('masks nested credential fields and token text without mutating the original record', () => {
    const original = { title: 'VPN 排查', details: { 密码: 'DemoNestedPassword123', access_token: 'DemoAccessToken123' },
      trace: [{ answer: '旧记录 password=DemoPassword42', code: 'VPN-403' }], count: 2, unknown: null };
    const redacted = redactSensitiveValue(original);
    expect(redacted).toMatchObject({ title: 'VPN 排查', count: 2, unknown: null,
      details: { 密码: '[已遮蔽]', access_token: '[已遮蔽]' } });
    expect(redacted.trace[0].code).toBe('VPN-403');
    expect(JSON.stringify(redacted)).not.toMatch(/DemoNestedPassword123|DemoAccessToken123|DemoPassword42/);
    expect(original.details.密码).toBe('DemoNestedPassword123');
  });

  it('masks recognized credentials carried in an error message', () => {
    const error = new ApiError('test_failure', '上游返回 password=DemoErrorPassword42');
    expect(error.message).not.toContain('DemoErrorPassword42');
    expect(error.code).toBe('test_failure');
  });
});
