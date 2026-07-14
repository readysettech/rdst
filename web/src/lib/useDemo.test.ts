import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useDemo } from './useDemo';

function jsonResponse(body: unknown) {
  return {
    ok: true,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    body: null,
  } as unknown as Response;
}

function mockFetch(status: Record<string, unknown>) {
  return vi.fn((url: RequestInfo | URL) => {
    const u = String(url);
    if (u.includes('/status')) return Promise.resolve(jsonResponse(status));
    if (u.includes('/history')) return Promise.resolve(jsonResponse({ samples: [], events: [] }));
    if (u.includes('/patterns')) return Promise.resolve(jsonResponse({ patterns: [] }));
    return Promise.resolve(jsonResponse({}));
  });
}

const querypilot = { enabled: false, mode: 'count_star', next_pass_eta_s: null, schedule: '15s', cache_budget: 10 };

describe('useDemo provisioned derivation', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('stays idle when the backend reports unprovisioned despite stale running health', async () => {
    vi.stubGlobal('fetch', mockFetch({
      provisioned: false,
      ports: null,
      health: { pg: 'failed', readyset: 'failed', sqp: 'failed', 'qp-cron': 'running' },
      querypilot,
      load_running: false,
      cache_budget: 10,
      last_error: null,
    }));
    const { result } = renderHook(() => useDemo());
    await waitFor(() => expect(result.current.phase).toBe('idle'));
  });

  it('becomes ready when the backend reports provisioned', async () => {
    vi.stubGlobal('fetch', mockFetch({
      provisioned: true,
      ports: { pg: 5432 },
      health: { pg: 'running', readyset: 'running', sqp: 'running', 'qp-cron': 'running' },
      querypilot,
      load_running: false,
      cache_budget: 10,
      last_error: null,
    }));
    const { result } = renderHook(() => useDemo());
    await waitFor(() => expect(result.current.phase).toBe('ready'));
  });
});
