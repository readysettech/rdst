import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetBackgroundRunsForTests,
  acknowledgeBackgroundRun,
  cancelBackgroundRun,
  clearAllBackgroundRuns,
  dismissBackgroundRun,
  getBackgroundRuns,
  reattachBackgroundRuns,
  startBootstrapRun,
  startCacheTestRun,
  startSchemaAnnotationRun,
} from './backgroundRuns'
import { api } from './client'

vi.mock('./client', () => ({
  api: { POST: vi.fn(), GET: vi.fn(), DELETE: vi.fn() },
}))

function sseResponse(payload: string): Response {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

function frames(...items: Array<[string, Record<string, unknown>]>): string {
  return items
    .map(
      ([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
    )
    .join('')
}

function run(runId: string) {
  return getBackgroundRuns().find((item) => item.runId === runId)
}

describe('background run store', () => {
  beforeEach(() => {
    __resetBackgroundRunsForTests({ reconnectBaseMs: 5 })
    vi.mocked(api.POST).mockImplementation(
      async (path: string) =>
        ({
          data: {
            run_id:
              path === '/api/bootstrap'
                ? 'bootstrap_imdb_x'
                : path === '/api/cache/test-runs'
                  ? 'cache_test_imdb_z'
                  : 'schema_annotation_imdb_y',
          },
          error: undefined,
        }) as never
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: {
        run_id: 'bootstrap_imdb_x',
        kind: 'bootstrap',
        target: 'imdb',
        status: 'running',
        last_seq: 2,
      },
      error: undefined,
    } as never)
    vi.mocked(api.DELETE).mockResolvedValue({
      data: { run_id: 'bootstrap_imdb_x', cancelled: true },
      error: undefined,
    } as never)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('starts bootstrap and follows the common event stream to completion', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sseResponse(
          frames(
            [
              'bootstrap_stage',
              { stage: 'structure', message: 'Reading schema', seq: 1 },
            ],
            ['run_end', { status: 'done', seq: 2 }]
          )
        )
      )
    vi.stubGlobal('fetch', fetchMock)

    startBootstrapRun('imdb')

    await waitFor(() => expect(run('bootstrap_imdb_x')?.status).toBe('done'))
    expect(localStorage.getItem('rdst_background_runs')).toContain(
      'bootstrap_imdb_x'
    )
    expect(api.POST).toHaveBeenCalledWith('/api/bootstrap', {
      body: { target: 'imdb', deploy: true, deploy_mode: 'docker' },
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/bootstrap_imdb_x/events?after_seq=0',
      { signal: expect.any(AbortSignal) }
    )
  })

  it('clears every run and its persisted record on local data reset', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sseResponse(
          frames([
            'bootstrap_stage',
            { stage: 'structure', message: 'Reading schema', seq: 1 },
          ])
        )
      )
    vi.stubGlobal('fetch', fetchMock)

    startBootstrapRun('imdb')
    await waitFor(() =>
      expect(run('bootstrap_imdb_x')?.status).toBeDefined()
    )
    expect(localStorage.getItem('rdst_background_runs')).toContain(
      'bootstrap_imdb_x'
    )

    clearAllBackgroundRuns()

    expect(getBackgroundRuns()).toEqual([])
    expect(localStorage.getItem('rdst_background_runs')).toBeNull()
  })

  it('tracks table progress for manual annotation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse(
          frames(
            [
              'annotate_started',
              {
                tables: 25,
                completed_tables: 9,
                message: 'Resuming annotation: 9 of 25 tables already complete',
                seq: 1,
              },
            ],
            [
              'annotate_progress',
              {
                table: 'catalog_sales',
                table_index: 10,
                total_tables: 25,
                message: 'Annotating catalog_sales',
                seq: 2,
              },
            ],
            ['run_end', { status: 'done', seq: 3 }]
          )
        )
      )
    )

    const runId = await startSchemaAnnotationRun('imdb')

    expect(runId).toBe('schema_annotation_imdb_y')
    await waitFor(() =>
      expect(run('schema_annotation_imdb_y')?.status).toBe('done')
    )
    expect(run('schema_annotation_imdb_y')).toMatchObject({
      kind: 'schema_annotation',
      target: 'imdb',
      current: 10,
      total: 25,
      message: 'Annotating catalog_sales',
      lastSeq: 3,
    })
  })

  it('keeps a completed cache comparison and its query navigation metadata', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse(
          frames(
            [
              'progress',
              {
                stage: 'origin',
                percent: 40,
                message: 'Benchmarking origin (2/5)',
                seq: 1,
              },
            ],
            [
              'cache_run_complete',
              {
                success: true,
                query: 'SELECT 1',
                iterations: 3,
                origin_stats: {
                  mean: 20,
                  median: 19,
                  min: 15,
                  max: 30,
                  p50: 19,
                  p95: 28,
                  p99: 30,
                },
                cache_stats: {
                  mean: 1,
                  median: 1,
                  min: 0.5,
                  max: 2,
                  p50: 1,
                  p95: 1.8,
                  p99: 2,
                },
                speedup_mean: 20,
                speedup_median: 19,
                improvement_pct: 1900,
                winner: 'readyset',
                seq: 2,
              },
            ],
            ['run_end', { status: 'done', seq: 3 }]
          )
        )
      )
    )

    const runId = await startCacheTestRun({
      target: 'imdb',
      query: 'SELECT 1',
      query_hash: 'abc123',
      label: 'Health check',
      iterations: 3,
      warmup: 1,
    })

    expect(runId).toBe('cache_test_imdb_z')
    await waitFor(() => expect(run('cache_test_imdb_z')?.status).toBe('done'))
    expect(run('cache_test_imdb_z')).toMatchObject({
      kind: 'cache_test',
      queryHash: 'abc123',
      queryLabel: 'Health check',
      message: 'Performance test complete',
      result: { speedup_mean: 20, winner: 'readyset' },
    })
    expect(localStorage.getItem('rdst_background_runs')).toContain(
      'cache_test_imdb_z'
    )
  })

  it('keeps partial annotation completion as a warning terminal state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse(
          frames(
            [
              'annotate_started',
              {
                tables: 24,
                completed_tables: 0,
                message: 'Starting annotation',
                seq: 1,
              },
            ],
            [
              'annotate_complete',
              {
                success: false,
                tables_annotated: 23,
                columns_annotated: 100,
                tables_failed: 1,
                message: 'Annotated 23 tables; 1 failed',
                seq: 2,
              },
            ],
            ['run_end', { status: 'done', seq: 3 }]
          )
        )
      )
    )

    await startSchemaAnnotationRun('imdb')

    await waitFor(() =>
      expect(run('schema_annotation_imdb_y')?.status).toBe('partial')
    )
    expect(run('schema_annotation_imdb_y')?.message).toBe(
      'Annotated 23 tables; 1 failed'
    )
  })

  it('keeps a bootstrap annotation warning through the terminal frame', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse(
          frames(
            [
              'bootstrap_stage',
              {
                stage: 'annotate',
                status: 'failed',
                message: 'Annotated 23 tables; 1 failed',
                seq: 1,
              },
            ],
            [
              'bootstrap_stage',
              {
                stage: 'deploy',
                status: 'done',
                message: 'Readyset ready',
                seq: 2,
              },
            ],
            ['run_end', { status: 'done', seq: 3 }]
          )
        )
      )
    )

    startBootstrapRun('imdb')

    await waitFor(() => expect(run('bootstrap_imdb_x')?.status).toBe('partial'))
    expect(run('bootstrap_imdb_x')?.hasWarnings).toBe(true)
    expect(run('bootstrap_imdb_x')?.message).toBe(
      'Annotated 23 tables; 1 failed'
    )
  })

  it('keeps multiple active runs in reload storage', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))

    startBootstrapRun('imdb')
    await startSchemaAnnotationRun('analytics')

    await waitFor(() => expect(getBackgroundRuns()).toHaveLength(2))
    const stored = JSON.parse(
      localStorage.getItem('rdst_background_runs') ?? '[]'
    )
    expect(
      stored.map((item: { target: string }) => item.target).sort()
    ).toEqual(['analytics', 'imdb'])
  })

  it('reattaches every stored run from its last sequence', async () => {
    localStorage.setItem(
      'rdst_background_runs',
      JSON.stringify([
        {
          runId: 'schema_annotation_imdb_y',
          kind: 'schema_annotation',
          target: 'imdb',
          stage: 'annotate',
          status: 'running',
          message: 'Annotating',
          lastSeq: 5,
          current: 4,
          total: 10,
        },
      ])
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: {
        run_id: 'schema_annotation_imdb_y',
        kind: 'schema_annotation',
        target: 'imdb',
        status: 'running',
        last_seq: 6,
      },
      error: undefined,
    } as never)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sseResponse(frames(['run_end', { status: 'done', seq: 6 }]))
      )
    vi.stubGlobal('fetch', fetchMock)

    reattachBackgroundRuns()

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/runs/schema_annotation_imdb_y/events?after_seq=5',
        { signal: expect.any(AbortSignal) }
      )
    )
  })

  it('keeps reconnecting beyond the old retry limit until the run completes', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(
        sseResponse(frames(['run_end', { status: 'done', seq: 1 }]))
      )
    vi.stubGlobal('fetch', fetchMock)

    startBootstrapRun('imdb')

    await waitFor(() => expect(run('bootstrap_imdb_x')?.status).toBe('done'))
    expect(fetchMock).toHaveBeenCalledTimes(7)
    expect(run('bootstrap_imdb_x')?.message).not.toContain('Lost connection')
  })

  it('reconnects after a temporary stream failure and replays the terminal result', async () => {
    let finishReconnect: (response: Response) => void = () => {}
    const replay = new Promise<Response>((resolve) => {
      finishReconnect = resolve
    })
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockReturnValueOnce(replay)
    vi.stubGlobal('fetch', fetchMock)

    await startCacheTestRun({
      target: 'imdb',
      query: 'SELECT 1',
      query_hash: 'abc123',
      label: 'Health check',
      iterations: 3,
      warmup: 1,
    })

    await waitFor(() =>
      expect(run('cache_test_imdb_z')?.status).toBe('reconnecting')
    )
    finishReconnect(
      sseResponse(
        frames(
          [
            'cache_run_complete',
            {
              success: true,
              speedup_mean: 5,
              winner: 'readyset',
              seq: 1,
            },
          ],
          ['run_end', { status: 'done', seq: 2 }]
        )
      )
    )

    await waitFor(() => expect(run('cache_test_imdb_z')?.status).toBe('done'))
    expect(run('cache_test_imdb_z')?.result).toMatchObject({
      speedup_mean: 5,
      winner: 'readyset',
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('migrates an old interrupted run and reattaches it on reload', async () => {
    localStorage.setItem(
      'rdst_background_runs',
      JSON.stringify([
        {
          runId: 'bootstrap_imdb_x',
          kind: 'bootstrap',
          target: 'imdb',
          stage: 'profile',
          status: 'interrupted',
          message: 'Lost connection to the run',
          lastSeq: 2,
          current: null,
          total: null,
          hasWarnings: false,
        },
      ])
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: {
        run_id: 'bootstrap_imdb_x',
        kind: 'bootstrap',
        target: 'imdb',
        status: 'done',
        last_seq: 3,
      },
      error: undefined,
      response: new Response(null, { status: 200 }),
    } as never)
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse(frames(['run_end', { status: 'done', seq: 3 }]))
        )
    )

    reattachBackgroundRuns()

    await waitFor(() => expect(run('bootstrap_imdb_x')?.status).toBe('done'))
    expect(api.GET).toHaveBeenCalledOnce()
  })

  it('retains stored terminal results without probing the backend', () => {
    localStorage.setItem(
      'rdst_background_runs',
      JSON.stringify([
        {
          runId: 'cache_test_imdb_done',
          kind: 'cache_test',
          target: 'imdb',
          stage: 'complete',
          status: 'done',
          message: 'Performance test complete',
          lastSeq: 4,
          current: 100,
          total: 100,
          hasWarnings: false,
          queryHash: 'abc123',
        },
      ])
    )

    reattachBackgroundRuns()

    expect(run('cache_test_imdb_done')?.status).toBe('done')
    expect(api.GET).not.toHaveBeenCalled()
  })

  it('forgets a stored run after the backend process restarts', async () => {
    localStorage.setItem(
      'rdst_background_runs',
      JSON.stringify([
        {
          runId: 'schema_annotation_gone',
          kind: 'schema_annotation',
          target: 'imdb',
          stage: 'annotate',
          status: 'running',
          message: 'Annotating',
          lastSeq: 2,
          current: 1,
          total: 10,
        },
      ])
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: undefined,
      error: { detail: 'not found' },
      response: new Response(null, { status: 404 }),
    } as never)
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    reattachBackgroundRuns()

    await waitFor(() => expect(getBackgroundRuns()).toHaveLength(0))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('replays a failure that finished before the reattach status probe', async () => {
    localStorage.setItem(
      'rdst_background_runs',
      JSON.stringify([
        {
          runId: 'schema_annotation_imdb_y',
          kind: 'schema_annotation',
          target: 'imdb',
          stage: 'annotate',
          status: 'running',
          message: 'Annotating title',
          lastSeq: 4,
          current: 4,
          total: 10,
        },
      ])
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: {
        run_id: 'schema_annotation_imdb_y',
        kind: 'schema_annotation',
        target: 'imdb',
        status: 'failed',
        last_seq: 6,
      },
      error: undefined,
      response: new Response(null, { status: 200 }),
    } as never)
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse(
            frames(
              [
                'annotate_error',
                { message: 'Provider rejected the key', seq: 5 },
              ],
              ['run_end', { status: 'failed', seq: 6 }]
            )
          )
        )
    )

    reattachBackgroundRuns()

    await waitFor(() =>
      expect(run('schema_annotation_imdb_y')?.status).toBe('failed')
    )
    expect(run('schema_annotation_imdb_y')?.message).toBe(
      'Provider rejected the key'
    )
  })

  it('surfaces annotation errors as failed terminal runs', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse(
            frames(
              [
                'annotate_error',
                { message: 'Anthropic rejected the key', seq: 1 },
              ],
              ['run_end', { status: 'failed', seq: 2 }]
            )
          )
        )
    )

    await startSchemaAnnotationRun('imdb')

    await waitFor(() =>
      expect(run('schema_annotation_imdb_y')?.status).toBe('failed')
    )
    expect(run('schema_annotation_imdb_y')?.message).toBe(
      'Anthropic rejected the key'
    )
  })

  it('cancels and dismisses a run by id', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))
    startBootstrapRun('imdb')
    await waitFor(() => expect(run('bootstrap_imdb_x')).toBeDefined())

    await cancelBackgroundRun('bootstrap_imdb_x')
    expect(api.DELETE).toHaveBeenCalledWith('/api/runs/{run_id}', {
      params: { path: { run_id: 'bootstrap_imdb_x' } },
    })

    dismissBackgroundRun('bootstrap_imdb_x')
    expect(run('bootstrap_imdb_x')).toBeUndefined()
  })

  it('acknowledges a terminal job without deleting its result', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse(frames(['run_end', { status: 'done', seq: 1 }]))
        )
    )
    startBootstrapRun('imdb')
    await waitFor(() => expect(run('bootstrap_imdb_x')?.status).toBe('done'))

    acknowledgeBackgroundRun('bootstrap_imdb_x')

    expect(run('bootstrap_imdb_x')).toMatchObject({
      status: 'done',
      hidden: true,
    })
    expect(localStorage.getItem('rdst_background_runs')).toContain(
      'bootstrap_imdb_x'
    )
  })
})
