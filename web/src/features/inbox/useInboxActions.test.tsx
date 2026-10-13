import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { InboxItem } from '@/lib/monitor/types'
import { useInboxActions } from './useInboxActions'

const mocks = vi.hoisted(() => ({ mutateAsync: vi.fn(), toast: vi.fn() }))
vi.mock('@/lib/monitor/hooks', () => ({
  useInboxAction: () => ({ mutateAsync: mocks.mutateAsync, mutate: vi.fn() }),
}))
vi.mock('@rs/ui-new/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('@/lib/analytics', () => ({ trackEvent: vi.fn() }))
beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

function deferred() {
  let resolve!: () => void
  let reject!: (error: Error) => void
  const promise = new Promise<void>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
const item = (id: number) => ({ id, title: `Alert ${id}` }) as InboxItem

it('reveals each completed batch item while other mutations are pending', async () => {
  const first = deferred()
  const second = deferred()
  mocks.mutateAsync
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise)
  const { result } = renderHook(() => useInboxActions())
  act(() => result.current.close([item(1), item(2)], { kind: 'resolve' }))
  expect([...result.current.hidden]).toEqual([1, 2])
  await act(async () => first.resolve())
  expect([...result.current.hidden]).toEqual([2])
  await act(async () => second.reject(new Error('offline')))
  expect(result.current.hidden.size).toBe(0)
  expect(mocks.toast).toHaveBeenCalledWith(
    expect.objectContaining({ variant: 'negative' })
  )
})

it('keeps other batch items pending after one fails', async () => {
  const first = deferred()
  const second = deferred()
  mocks.mutateAsync
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise)
  const { result } = renderHook(() => useInboxActions())
  act(() => result.current.close([item(1), item(2)], { kind: 'resolve' }))
  await act(async () => first.reject(new Error('offline')))
  expect([...result.current.hidden]).toEqual([2])
  await act(async () => second.resolve())
  expect(result.current.hidden.size).toBe(0)
})

it.each([
  'success',
  'failure',
])('clears pending recovery acknowledgements on %s', async (outcome) => {
  const request = deferred()
  mocks.mutateAsync.mockReturnValueOnce(request.promise)
  const { result } = renderHook(() => useInboxActions())
  act(() => result.current.acknowledge([item(1)]))
  expect(result.current.hidden.has(1)).toBe(true)
  await act(async () => {
    if (outcome === 'success') request.resolve()
    else request.reject(new Error('offline'))
  })
  expect(result.current.hidden.size).toBe(0)
})
