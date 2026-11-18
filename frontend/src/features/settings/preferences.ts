export interface Preferences {
  theme: 'system' | 'light' | 'dark'; font_size: 'small' | 'standard' | 'large';
  density: 'comfortable' | 'compact'; show_examples: boolean; show_timestamps: boolean;
  show_metrics: boolean; expand_sources: boolean; expand_tools: boolean;
}
export const PREFERENCES_KEY = 'itdesk.preferences.v1';
export const DEFAULT_PREFERENCES: Preferences = {
  theme: 'system', font_size: 'standard', density: 'comfortable', show_examples: true,
  show_timestamps: false, show_metrics: false, expand_sources: false, expand_tools: false,
};
const choices: Partial<Record<keyof Preferences, readonly string[]>> = {
  theme: ['system', 'light', 'dark'], font_size: ['small', 'standard', 'large'], density: ['comfortable', 'compact'],
};
function valid(key: keyof Preferences, value: unknown): boolean {
  return choices[key] ? typeof value === 'string' && choices[key]!.includes(value) : typeof value === 'boolean';
}
export function loadPreferences(storage?: Storage): Preferences {
  const defaults = { ...DEFAULT_PREFERENCES };
  try {
    const parsed: unknown = JSON.parse((storage ?? window.localStorage).getItem(PREFERENCES_KEY) ?? 'null');
    if (!parsed || typeof parsed !== 'object' || !('version' in parsed) || parsed.version !== 1 || !('values' in parsed)) return defaults;
    const values = parsed.values;
    if (!values || typeof values !== 'object' || Array.isArray(values)) return defaults;
    const result: Record<string, unknown> = { ...defaults };
    for (const key of Object.keys(defaults) as (keyof Preferences)[]) {
      if (key in values && valid(key, values[key as keyof typeof values])) result[key] = values[key as keyof typeof values];
    }
    return result as unknown as Preferences;
  } catch { return defaults; }
}
export function savePreferences(values: Preferences, storage?: Storage): Preferences {
  if (!values || typeof values !== 'object' || Array.isArray(values) ||
      Object.keys(values).some(key => !(key in DEFAULT_PREFERENCES)) ||
      (Object.keys(DEFAULT_PREFERENCES) as (keyof Preferences)[]).some(key => !valid(key, values[key]))) {
    throw new Error('设置值或类型不合法，请重新选择。');
  }
  const saved = { ...values };
  try { (storage ?? window.localStorage).setItem(PREFERENCES_KEY, JSON.stringify({ version: 1, values: saved })); }
  catch { throw new Error('浏览器无法保存设置，请检查存储权限后重试；当前设置保持不变。'); }
  return saved;
}
export function resetPreferences(storage?: Storage): Preferences { return savePreferences({ ...DEFAULT_PREFERENCES }, storage); }
