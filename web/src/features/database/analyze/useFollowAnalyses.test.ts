import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { AnalyzeJob } from '@/lib/monitor/types'
import { createTestQueryClient, queryClientWrapper } from '@/test-utils'
import { activeAnalysisKeys, useFollowAnalyses } from './useFollowAnalyses'

const job = (
  query_key: string,
  state: AnalyzeJob['state'],
  queue_state: AnalyzeJob['queue_state'] = null
): AnalyzeJob =>
  ({ id: 1, target: 'shop', query_key, state, queue_state }) as AnalyzeJob

const invalidated = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map(([filters]) => (filters as { queryKey: unknown }).queryKey)

describe('useFollowAnalyses', () => {
  it('counts running and queued jobs, not held or finished ones', () => {
    expect([
      ...activeAnalysisKeys([
        job('a', 'running'),
        job('b', 'queued', 'today'),
        job('c', 'queued', 'held'),
        job('d', 'done'),
      ]),
    ]).toEqual(['a', 'b'])
  })

  it('refreshes the list, the row, the digest and the plan when an analysis finishes', () => {
    const client = createTestQueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    const { rerender } = renderHook(
      ({ jobs }) => useFollowAnalyses('shop', jobs),
      {
        wrapper: queryClientWrapper(client),
        initialProps: { jobs: [job('k1', 'running')] },
      }
    )
    expect(spy).not.toHaveBeenCalled()

    rerender({ jobs: [job('k1', 'done')] })

    expect(invalidated(spy)).toEqual(
      expect.arrayContaining([
        ['monitor', 'shop', 'queries'],
        ['monitor', 'shop', 'query'],
        ['monitor', 'shop', 'query-analysis', 'k1'],
        ['monitor', 'shop', 'actions'],
        ['monitor', 'shop', 'index-plan'],
        ['monitor', 'actions'],
      ])
    )
  })

  it('refreshes after a job started here that finished before the list saw it run', () => {
    const client = createTestQueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    const { result, rerender } = renderHook(
      ({ jobs }) => useFollowAnalyses('shop', jobs),
      {
        wrapper: queryClientWrapper(client),
        initialProps: { jobs: [] as AnalyzeJob[] },
      }
    )

    act(() => result.current.follow('k2'))
    spy.mockClear()
    rerender({ jobs: [job('k2', 'done')] })

    expect(invalidated(spy)).toContainEqual([
      'monitor',
      'shop',
      'query-analysis',
      'k2',
    ])
  })
})
