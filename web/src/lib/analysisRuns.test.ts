import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  __resetAnalysisRunsForTests,
  analysisRunKey,
  setAnalysisRunObserver,
  useAnalysisRunForQuery,
} from './analysisRuns'
import {
  __resetBackgroundRunsForTests,
  getBackgroundRuns,
} from './backgroundRuns'
import { useAnalyze } from './sse'
import { __resetTargetSwitchLockForTests } from './targetSwitchLock'

const REQUEST = { query: 'SELECT 1', target: 'prod' }

const COMPLETE_FRAME = `event: complete\ndata: ${JSON.stringify({
  success: true,
  analysis_id: 'a1',
  query_hash: 'h1',
  explain_results: {},
  llm_analysis: {},
})}\n\n`

/** An analyze stream the test finishes by hand, mid-run. */
function stubOpenStream() {
  let push: (chunk: string) => void = () => {}
  let finish: () => void = () => {}
  const body = new ReadableStream({
    start(controller) {
      push = (chunk) => controller.enqueue(new TextEncoder().encode(chunk))
      finish = () => controller.close()
    },
  })
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(new Response(body, { status: 200 }))
  )
  return {
    complete: async () => {
      await act(async () => {
        push(COMPLETE_FRAME)
        finish()
      })
    },
  }
}

afterEach(() => {
  __resetAnalysisRunsForTests()
  __resetBackgroundRunsForTests()
  __resetTargetSwitchLockForTests()
  vi.unstubAllGlobals()
})

describe('analyze runs outside the view that started them', () => {
  it('keeps running after unmount and reattaches by key', async () => {
    const stream = stubOpenStream()

    const starter = renderHook(() => useAnalyze())
    await act(async () => {
      await starter.result.current.analyze(REQUEST)
    })
    expect(starter.result.current.state).toBe('analyzing')

    // The drawer closes; the run does not.
    starter.unmount()

    const reopened = renderHook(() => useAnalyze(analysisRunKey(REQUEST)))
    expect(reopened.result.current.state).toBe('analyzing')

    await stream.complete()

    await waitFor(() => expect(reopened.result.current.state).toBe('complete'))
    expect(reopened.result.current.results?.analysis_id).toBe('a1')
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
  })

  it('starts idle without a key, so no view adopts a run by accident', async () => {
    stubOpenStream()

    const starter = renderHook(() => useAnalyze())
    await act(async () => {
      await starter.result.current.analyze(REQUEST)
    })
    starter.unmount()

    const unrelated = renderHook(() => useAnalyze())
    expect(unrelated.result.current.state).toBe('idle')
  })

  it('reports a finished run so stored-analysis reads can refresh', async () => {
    const stream = stubOpenStream()
    const observer = vi.fn()
    setAnalysisRunObserver(observer)

    const view = renderHook(() => useAnalyze())
    await act(async () => {
      await view.result.current.analyze(REQUEST)
    })
    view.unmount()

    await stream.complete()

    await waitFor(() => expect(observer).toHaveBeenCalledWith('h1'))
  })

  it('separates runs by query, target and depth', () => {
    expect(analysisRunKey(REQUEST)).not.toBe(
      analysisRunKey({ ...REQUEST, target: 'staging' })
    )
    expect(analysisRunKey(REQUEST)).not.toBe(
      analysisRunKey({ ...REQUEST, fast: true })
    )
    expect(analysisRunKey(REQUEST)).toBe(analysisRunKey({ ...REQUEST }))
  })
})

describe('a run a closed view can still be found by', () => {
  it('answers a card that knows only the registry hash', async () => {
    const stream = stubOpenStream()

    const starter = renderHook(() => useAnalyze())
    await act(async () => {
      await starter.result.current.analyze(REQUEST, { queryHash: 'h1' })
    })
    starter.unmount()

    // The card never sees the substituted SQL the drawer measured, so the
    // hash is the only identity it can ask with.
    const card = renderHook(() =>
      useAnalysisRunForQuery({ hash: 'h1', sql: 'SELECT 2', target: 'prod' })
    )
    expect(card.result.current?.state).toBe('analyzing')

    await stream.complete()
    await waitFor(() => expect(card.result.current?.state).toBe('complete'))
  })

  it('answers by request identity for a run started without a hash', async () => {
    stubOpenStream()

    const starter = renderHook(() => useAnalyze())
    await act(async () => {
      await starter.result.current.analyze(REQUEST)
    })

    const card = renderHook(() =>
      useAnalysisRunForQuery({
        hash: 'unknown',
        sql: ' SELECT 1 ',
        target: 'prod',
      })
    )
    expect(card.result.current?.state).toBe('analyzing')
  })
})

describe('analyze runs in the jobs sidebar', () => {
  it('registers on start and reports completion', async () => {
    const stream = stubOpenStream()

    const view = renderHook(() => useAnalyze())
    await act(async () => {
      await view.result.current.analyze(REQUEST, {
        queryHash: 'h1',
        queryLabel: 'Orders lookup',
      })
    })

    const started = getBackgroundRuns().filter((run) => run.kind === 'analyze')
    expect(started).toHaveLength(1)
    expect(started[0]).toMatchObject({
      status: 'running',
      target: 'prod',
      queryHash: 'h1',
      queryLabel: 'Orders lookup',
      local: true,
    })

    // Closing the view leaves the job listed, which is the point.
    view.unmount()
    await stream.complete()

    await waitFor(() => {
      const [job] = getBackgroundRuns().filter((run) => run.kind === 'analyze')
      expect(job?.status).toBe('done')
    })
    expect(localStorage.getItem('rdst_background_runs')).toBeNull()
  })

  it('replaces the job when the same query is measured again', async () => {
    stubOpenStream()

    const view = renderHook(() => useAnalyze())
    await act(async () => {
      await view.result.current.analyze(REQUEST, { queryHash: 'h1' })
    })
    await act(async () => {
      await view.result.current.analyze(REQUEST, { queryHash: 'h1' })
    })

    expect(
      getBackgroundRuns().filter((run) => run.kind === 'analyze')
    ).toHaveLength(1)
  })
})
