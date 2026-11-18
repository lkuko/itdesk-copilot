import { describe, expect, it } from 'vitest';
import {
  DEFAULT_PREFERENCES, PREFERENCES_KEY, loadPreferences, resetPreferences, savePreferences,
} from '../src/features/settings/preferences';

describe('browser display preference persistence', () => {
  it('uses defaults without mutating storage when the key is absent', () => {
    expect(loadPreferences()).toEqual(DEFAULT_PREFERENCES);
    expect(localStorage.getItem(PREFERENCES_KEY)).toBeNull();
  });

  it('round trips all eight display settings', () => {
    const selected = {
      theme: 'dark', font_size: 'large', density: 'compact', show_examples: false,
      show_timestamps: true, show_metrics: true, expand_sources: true, expand_tools: true,
    } as const;
    savePreferences(selected);
    expect(loadPreferences()).toEqual(selected);
  });

  it.each(['{broken', 'null', '[]', '"dark"'])('safely ignores malformed settings %s', (raw) => {
    localStorage.setItem(PREFERENCES_KEY, raw);
    expect(loadPreferences()).toEqual(DEFAULT_PREFERENCES);
    expect(localStorage.getItem(PREFERENCES_KEY)).toBe(raw);
  });

  it('ignores unknown fields and rejects illegal field types on read', () => {
    localStorage.setItem(PREFERENCES_KEY, JSON.stringify({ version: 1, values: {
      ...DEFAULT_PREFERENCES, theme: 'unknown', show_metrics: 'true', show_timestamps: true,
      api_key: 'not-a-supported-setting',
    } }));
    expect(loadPreferences()).toEqual({ ...DEFAULT_PREFERENCES, show_timestamps: true });
    expect(loadPreferences()).not.toHaveProperty('api_key');
  });

  it('does not interpret a different schema version as current settings', () => {
    localStorage.setItem(PREFERENCES_KEY, JSON.stringify({ version: 2, values: {
      ...DEFAULT_PREFERENCES, theme: 'dark',
    } }));
    expect(loadPreferences()).toEqual(DEFAULT_PREFERENCES);
  });

  it.each([
    { ...DEFAULT_PREFERENCES, theme: 'rainbow' },
    { ...DEFAULT_PREFERENCES, show_metrics: 'true' },
    { ...DEFAULT_PREFERENCES, api_key: 'not-a-preference' },
  ])('refuses invalid writes before replacing saved preferences', (values) => {
    savePreferences(DEFAULT_PREFERENCES);
    const before = localStorage.getItem(PREFERENCES_KEY);
    expect(() => savePreferences(values as unknown as typeof DEFAULT_PREFERENCES)).toThrow();
    expect(localStorage.getItem(PREFERENCES_KEY)).toBe(before);
  });

  it('restores defaults without clearing unrelated browser data', () => {
    localStorage.setItem('unrelated-project-data', 'keep');
    savePreferences({ ...DEFAULT_PREFERENCES, theme: 'dark' });
    resetPreferences();
    expect(loadPreferences()).toEqual(DEFAULT_PREFERENCES);
    expect(localStorage.getItem('unrelated-project-data')).toBe('keep');
  });

  it('reads unavailable browser storage as defaults', () => {
    const unavailable = {
      getItem() { throw new DOMException('Denied', 'SecurityError'); },
    } as unknown as Storage;
    expect(loadPreferences(unavailable)).toEqual(DEFAULT_PREFERENCES);
  });

  it('reports a storage write error so the form can keep applied settings unchanged', () => {
    const unavailable = {
      setItem() { throw new DOMException('Quota exceeded', 'QuotaExceededError'); },
    } as unknown as Storage;
    expect(() => savePreferences({ ...DEFAULT_PREFERENCES, theme: 'dark' }, unavailable)).toThrow();
    expect(loadPreferences()).toEqual(DEFAULT_PREFERENCES);
  });
});
