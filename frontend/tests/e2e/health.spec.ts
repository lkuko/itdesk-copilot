import { expect, test } from '@playwright/test';

test('real FastAPI health is reachable from the allowed frontend origin without model calls', async ({ request }) => {
  const response = await request.get('http://127.0.0.1:8000/api/health', {
    headers: { Origin: 'http://127.0.0.1:5173' },
  });
  expect(response.status()).toBe(200);
  expect(response.headers()['access-control-allow-origin']).toBe('http://127.0.0.1:5173');
  const health = await response.json();
  expect(['ok', 'not_ready']).toContain(health.status);
  expect(health.mode).toBe('local_demo_single_user');
  expect(health.agent.ticket_write).toBe('explicit_confirmation');
  expect(JSON.stringify(health)).not.toMatch(/api_key|authorization|bearer /i);
});
