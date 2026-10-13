import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { createTestQueryClient, queryClientWrapper } from '@/test-utils'
import { setFixtureOverride } from './contract'
import { analysisDidntStart, useEnqueueAnalysis } from './hooks'

const toast = vi.hoisted(() => vi.fn())
vi.mock('@rs/ui-new/use-toast', () => ({ toast }))

afterEach(() => {
  setFixtureOverride(undefined)
  vi.unstubAllGlobals()
  toast.mockReset()
})

it('says the analysis did not start when the backend refuses it', async () => {
  setFixtureOverride(null)
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'Daily limit reached' }), {
          status: 429,
        })
    )
  )
  const { result } = renderHook(() => useEnqueueAnalysis('demo'), {
    wrapper: queryClientWrapper(createTestQueryClient()),
  })
  act(() => result.current.mutate('abc'))
  await waitFor(() => expect(toast).toHaveBeenCalled())
  expect(toast.mock.calls[0][0]).toMatchObject({ variant: 'warning' })
  expect(toast.mock.calls[0][0].title).toMatch(/^The analysis didn't start/)
})

it('words the failure from the error, or asks to try again', () => {
  expect(analysisDidntStart(new Error('Sign in to run deep analysis'))).toBe(
    "The analysis didn't start: Sign in to run deep analysis"
  )
  expect(analysisDidntStart(null)).toBe(
    "The analysis didn't start. Try again in a moment."
  )
})
