import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { queryClientWrapper } from '@/test-utils'
import { useSignInNeeded } from './DatabaseHeader'

const fetchAccountStatus = vi.fn()
vi.mock('@/lib/api', () => ({
  fetchAccountStatus: () => fetchAccountStatus(),
}))
vi.mock('@/components/TrialRegistrationDialog', () => ({
  TrialRegistrationDialog: () => null,
}))

describe('useSignInNeeded', () => {
  beforeEach(() => fetchAccountStatus.mockReset())

  it('asks to sign in only when AI is paused and the account is signed out', async () => {
    fetchAccountStatus.mockResolvedValue({ signed_in: false })
    const { result } = renderHook(() => useSignInNeeded('ai_paused'), {
      wrapper: queryClientWrapper(),
    })
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('stays hidden for a signed-in account even if the pulse is stale', async () => {
    fetchAccountStatus.mockResolvedValue({ signed_in: true })
    const { result } = renderHook(() => useSignInNeeded('ai_paused'), {
      wrapper: queryClientWrapper(),
    })
    await waitFor(() => expect(fetchAccountStatus).toHaveBeenCalled())
    expect(result.current).toBe(false)
  })

  it('stays hidden while monitoring is live', async () => {
    fetchAccountStatus.mockResolvedValue({ signed_in: false })
    const { result } = renderHook(() => useSignInNeeded('live'), {
      wrapper: queryClientWrapper(),
    })
    await waitFor(() => expect(fetchAccountStatus).toHaveBeenCalled())
    expect(result.current).toBe(false)
  })
})
