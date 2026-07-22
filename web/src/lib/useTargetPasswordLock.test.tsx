import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fetchEnvRequirements, fetchStatus } from './api'
import { useTargetPasswordLock } from './useTargetPasswordLock'

vi.mock('./api', async () => {
  const actual = await vi.importActual('./api')
  return {
    ...actual,
    fetchStatus: vi.fn(),
    fetchEnvRequirements: vi.fn(),
  }
})

function wrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('useTargetPasswordLock', () => {
  it('returns locked state for selected target without password', async () => {
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: true,
      default_target: 'prod',
      targets: [{ name: 'prod', has_password: false, is_default: true }],
      version: '1.0.0',
      error: null,
    })
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: 'target_password',
          accepted_names: ['PROD_DB_PASSWORD'],
          target: 'prod',
          satisfied: false,
          source: 'missing',
        },
      ],
    })

    const { result } = renderHook(() => useTargetPasswordLock('prod'), {
      wrapper: wrapper(),
    })

    await waitFor(() => {
      expect(result.current.isResolved).toBe(true)
      expect(result.current.isLocked).toBe(true)
      expect(result.current.targetName).toBe('prod')
      expect(result.current.missingTargetRequirements).toHaveLength(1)
      expect(result.current.keyringAvailable).toBe(true)
    })
  })

  it('returns unlocked state when target password exists', async () => {
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: true,
      default_target: 'prod',
      targets: [{ name: 'prod', has_password: true, is_default: true }],
      version: '1.0.0',
      error: null,
    })
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: false,
      requirements: [],
    })

    const { result } = renderHook(() => useTargetPasswordLock('prod'), {
      wrapper: wrapper(),
    })

    await waitFor(() => {
      expect(result.current.isResolved).toBe(true)
      expect(result.current.isLocked).toBe(false)
      expect(result.current.targetName).toBe('prod')
      expect(result.current.missingTargetRequirements).toHaveLength(0)
    })
  })
})
