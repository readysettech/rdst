import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { useAccountStatusWatch } from './accountStatus'
import { type AccountStatusResponse, fetchAccountStatus } from './api'
import { invalidateTrialRelatedQueries } from './trialQueries'

vi.mock('./api', async () => {
  const actual = await vi.importActual('./api')
  return { ...actual, fetchAccountStatus: vi.fn() }
})
vi.mock('./trialQueries', () => ({
  invalidateTrialRelatedQueries: vi.fn(async () => {}),
}))

const status = (signedIn: boolean) =>
  ({ signed_in: signedIn }) as AccountStatusResponse

describe('useAccountStatusWatch', () => {
  it('refreshes the AI gates once a sign-in finishes elsewhere', async () => {
    vi.mocked(fetchAccountStatus).mockResolvedValue(status(false))
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useAccountStatusWatch(), { wrapper })
    await waitFor(() => expect(result.current.data?.signed_in).toBe(false))
    expect(invalidateTrialRelatedQueries).not.toHaveBeenCalled()

    vi.mocked(fetchAccountStatus).mockResolvedValue(status(true))
    await queryClient.refetchQueries({ queryKey: ['account-status'] })
    await waitFor(() =>
      expect(invalidateTrialRelatedQueries).toHaveBeenCalledTimes(1)
    )
  })

  it('leaves the AI gates alone when the app opens signed in', async () => {
    vi.mocked(invalidateTrialRelatedQueries).mockClear()
    vi.mocked(fetchAccountStatus).mockResolvedValue(status(true))
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useAccountStatusWatch(), { wrapper })
    await waitFor(() => expect(result.current.data?.signed_in).toBe(true))
    expect(invalidateTrialRelatedQueries).not.toHaveBeenCalled()
  })
})
