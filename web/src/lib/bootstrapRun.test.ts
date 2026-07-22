import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetBootstrapRunForTests,
  dismissBootstrapRun,
  getBootstrapRunState,
  reattachBootstrapRun,
  startBootstrapRun,
} from './bootstrapRun'
import { api } from './client'

vi.mock('./client', () => ({
  api: { POST: vi.fn(), GET: vi.fn() },
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
    .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join('')
}

const RUN_DONE = frames(
  ['bootstrap_stage', { stage: 'connection_test', status: 'started', seq: 1 }],
  ['bootstrap_stage', { stage: 'structure', status: 'done', seq: 2 }],
  ['run_end', { status: 'done', seq: 3 }]
)

describe('bootstrapRun store', () => {
  beforeEach(() => {
    __resetBootstrapRunForTests({ reconnectBaseMs: 10 })
    vi.mocked(api.POST).mockResolvedValue({
      data: { run_id: 'bootstrap_imdb_x_abc' },
      error: undefined,
    } as never)
    vi.mocked(api.GET).mockResolvedValue({
      data: { run_id: 'bootstrap_imdb_y_def', status: 'running' },
      error: undefined,
    } as never)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('start posts, streams to done, and clears storage on terminal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(RUN_DONE))
    vi.stubGlobal('fetch', fetchMock)

    startBootstrapRun('imdb', { deploy: true })

    expect(await waitFor(() => getBootstrapRunState().status)).toBeDefined()
    await waitFor(() => {
      expect(getBootstrapRunState().status).toBe('done')
    })
    expect(api.POST).toHaveBeenCalledWith('/api/bootstrap', {
      body: { target: 'imdb', deploy: true, deploy_mode: 'docker' },
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/bootstrap/runs/bootstrap_imdb_x_abc/events?after_seq=0'
    )
    expect(getBootstrapRunState().lastSeq).toBe(3)
    // Terminal runs leave no stored key: presence implies resumable.
    expect(localStorage.getItem('rdst_bootstrap_run')).toBeNull()
  })

  it('persists in-flight state for reload reattach', async () => {
    const gate = frames(['bootstrap_stage', { stage: 'profile', seq: 1 }])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(gate)))

    startBootstrapRun('imdb')

    await waitFor(() => {
      expect(getBootstrapRunState().stage).toBe('profile')
    })
    const stored = JSON.parse(localStorage.getItem('rdst_bootstrap_run') ?? '{}')
    expect(stored).toEqual({
      runId: 'bootstrap_imdb_x_abc',
      target: 'imdb',
      lastSeq: 1,
    })
    dismissBootstrapRun()
  })

  it('a failed kickoff surfaces on the chip without throwing', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      data: undefined,
      error: { detail: 'locked' },
    } as never)

    startBootstrapRun('imdb')

    await waitFor(() => {
      expect(getBootstrapRunState().status).toBe('failed')
    })
    expect(getBootstrapRunState().message).toBe('Setup could not start')
    expect(localStorage.getItem('rdst_bootstrap_run')).toBeNull()
  })

  it('reconnects with after_seq when the stream drops mid-run', async () => {
    const firstHalf = frames(
      ['bootstrap_stage', { stage: 'structure', status: 'started', seq: 1 }],
      ['bootstrap_stage', { stage: 'profile', status: 'started', seq: 2 }]
    )
    const secondHalf = frames(['run_end', { status: 'done', seq: 3 }])
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse(firstHalf))
      .mockResolvedValueOnce(sseResponse(secondHalf))
    vi.stubGlobal('fetch', fetchMock)

    startBootstrapRun('imdb')

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })
    expect(fetchMock.mock.calls[1][0]).toBe(
      '/api/bootstrap/runs/bootstrap_imdb_x_abc/events?after_seq=2'
    )
    await waitFor(() => {
      expect(getBootstrapRunState().status).toBe('done')
    })
  })

  it('needs_key parks the status until the next stage event', async () => {
    const parked = frames(
      ['bootstrap_stage', { stage: 'profile', status: 'done', seq: 1 }],
      ['needs_key', { message: 'Add a key', seq: 2 }]
    )
    const resumed = frames(
      ['bootstrap_stage', { stage: 'annotate', status: 'started', seq: 3 }],
      ['run_end', { status: 'done', seq: 4 }]
    )
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse(parked))
      .mockResolvedValueOnce(sseResponse(resumed))
    vi.stubGlobal('fetch', fetchMock)

    startBootstrapRun('imdb')

    await waitFor(() => {
      expect(getBootstrapRunState().status).toBe('done')
    })
    // The needs_key frame parked the run before annotate resumed it.
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('a 404 on the events stream settles as interrupted', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('nope', { status: 404 }))
    )

    startBootstrapRun('imdb')

    await waitFor(() => {
      expect(getBootstrapRunState().status).toBe('interrupted')
    })
  })

  it('reattach resumes a stored run from lastSeq', async () => {
    localStorage.setItem(
      'rdst_bootstrap_run',
      JSON.stringify({ runId: 'bootstrap_imdb_y_def', target: 'imdb', lastSeq: 5 })
    )
    const fetchMock = vi
      .fn()
      .mockResolvedValue(sseResponse(frames(['run_end', { status: 'done', seq: 6 }])))
    vi.stubGlobal('fetch', fetchMock)

    reattachBootstrapRun()

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/bootstrap/runs/bootstrap_imdb_y_def/events?after_seq=5'
      )
    })
  })

  it('reattach ignores empty and corrupt stored state', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    reattachBootstrapRun()
    expect(fetchMock).not.toHaveBeenCalled()

    localStorage.setItem('rdst_bootstrap_run', 'not json{{')
    reattachBootstrapRun()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reattach restores a parked needs_key run from server status', async () => {
    // Regression (found in live e2e): the stored lastSeq already covered the
    // needs_key frame, so replay returned nothing and the chip sat on
    // "Reconnecting..." forever. The server status is authoritative.
    localStorage.setItem(
      'rdst_bootstrap_run',
      JSON.stringify({ runId: 'bootstrap_imdb_y_def', target: 'imdb', lastSeq: 7 })
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: { run_id: 'bootstrap_imdb_y_def', status: 'needs_key' },
      error: undefined,
    } as never)
    // Parked run: the stream stays open and sends nothing; never resolves.
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))

    reattachBootstrapRun()

    await waitFor(() => {
      expect(getBootstrapRunState().status).toBe('needs_key')
    })
    expect(getBootstrapRunState().target).toBe('imdb')
  })

  it('reattach clears state when the server no longer knows the run', async () => {
    localStorage.setItem(
      'rdst_bootstrap_run',
      JSON.stringify({ runId: 'bootstrap_imdb_gone', lastSeq: 3 })
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: undefined,
      error: { detail: 'not found' },
    } as never)
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    reattachBootstrapRun()

    await waitFor(() => {
      expect(localStorage.getItem('rdst_bootstrap_run')).toBeNull()
    })
    expect(getBootstrapRunState().status).toBe('idle')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reattach to a finished run shows the terminal state without streaming', async () => {
    localStorage.setItem(
      'rdst_bootstrap_run',
      JSON.stringify({ runId: 'bootstrap_imdb_y_def', target: 'imdb', lastSeq: 9 })
    )
    vi.mocked(api.GET).mockResolvedValue({
      data: { run_id: 'bootstrap_imdb_y_def', status: 'done' },
      error: undefined,
    } as never)
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    reattachBootstrapRun()

    await waitFor(() => {
      expect(getBootstrapRunState().status).toBe('done')
    })
    expect(fetchMock).not.toHaveBeenCalled()
    // Terminal state leaves no stored key.
    expect(localStorage.getItem('rdst_bootstrap_run')).toBeNull()
  })

  it('dismiss clears state and storage', async () => {
    const gate = frames(['bootstrap_stage', { stage: 'profile', seq: 1 }])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(gate)))
    startBootstrapRun('imdb')
    await waitFor(() => {
      expect(localStorage.getItem('rdst_bootstrap_run')).not.toBeNull()
    })

    dismissBootstrapRun()

    expect(getBootstrapRunState().status).toBe('idle')
    expect(localStorage.getItem('rdst_bootstrap_run')).toBeNull()
  })
})
