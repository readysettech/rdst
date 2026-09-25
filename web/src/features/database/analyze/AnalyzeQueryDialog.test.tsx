import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import {
  __resetAnalysisRunsForTests,
  analysisRunKey,
  getAnalysisRun,
} from '@/lib/analysisRuns'
import { __resetBackgroundRunsForTests } from '@/lib/backgroundRuns'
import { renderWithClient, sseFrames } from '@/test-utils'

const api = vi.hoisted(() => ({
  add: vi.fn().mockResolvedValue({ key: 'filed' }),
}))
vi.mock('@/lib/monitor/api', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/api')>()),
  addQuery: api.add,
}))
vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useMonitorSettings: () => ({
    data: { enabled: true, analysis_mode: 'plan' },
  }),
}))
vi.mock('@/features/monitor-kit/ScrollDialog', () => ({
  ScrollDialog: ({
    open,
    children,
    footer,
    onClose,
  }: {
    open: boolean
    children: ReactNode
    footer: ReactNode
    onClose: () => void
  }) =>
    open ? (
      <div>
        <button type="button" onClick={onClose}>
          Dismiss
        </button>
        {children}
        {footer}
      </div>
    ) : null,
}))
vi.mock('@/features/queries/results/AnalysisResults', () => ({
  AnalysisResults: () => null,
}))

import { AnalyzeQueryDialog } from './AnalyzeQueryDialog'

afterEach(() => {
  cleanup()
  __resetAnalysisRunsForTests()
  __resetBackgroundRunsForTests()
  vi.unstubAllGlobals()
  api.add.mockClear()
})

it.each([
  'Close',
  'Dismiss',
])('RV3-02 %s preserves and files a run after reopening on another target and unmounting', async (dismiss) => {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start: (c) => {
      controller = c
    },
  })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)))
  const close = vi.fn()
  const props = {
    target: 'original',
    open: true,
    onClose: close,
    onShowQuery: vi.fn(),
    initialSql: 'SELECT 1',
  }
  const view = renderWithClient(<AnalyzeQueryDialog {...props} />)
  fireEvent.click(screen.getByRole('button', { name: 'Run deep analysis' }))
  await act(async () => {})
  const request = JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string)
  const key = analysisRunKey(request)
  fireEvent.click(screen.getByRole('button', { name: dismiss }))
  expect(close).toHaveBeenCalledOnce()
  expect(getAnalysisRun(key)?.state).toBe('analyzing')
  view.rerender(<AnalyzeQueryDialog {...props} open={false} />)
  view.rerender(
    <AnalyzeQueryDialog {...props} target="other" initialSql="SELECT 2" />
  )
  view.unmount()
  await act(async () => {
    controller.enqueue(
      new TextEncoder().encode(
        sseFrames([
          'complete',
          { success: true, query_hash: 'hash', stored_analysis_id: 'a1' },
        ])
      )
    )
    controller.close()
  })
  await waitFor(() => expect(api.add).toHaveBeenCalledOnce())
  expect(api.add).toHaveBeenCalledWith(
    'original',
    expect.objectContaining({
      sql: 'SELECT 1',
      query_hash: 'hash',
      analysis_id: 'a1',
      mode: request.fast ? 'explain' : 'analyze',
    })
  )
  expect(getAnalysisRun(key)?.state).toBe('complete')
})
