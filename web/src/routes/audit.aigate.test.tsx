import type { QueryClient } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createTestQueryClient, queryClientWrapper } from '@/test-utils'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { useAiGate } from '../lib/useAiGate'

// The gate reads three server facts; this fixture is the server, so a test can
// flip "key was just saved" between renders the way a real save does.
type ServerState = {
  source: string
  satisfied: boolean
  valid: boolean
  /** `/api/account/status`; a backend without free hosted AI omits signin_prompt. */
  account?: Record<string, unknown>
}

function serveEnv(state: ServerState) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input)
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      if (url.includes('/api/env/requirements')) {
        return json({
          keyring_available: true,
          telemetry_enabled: true,
          requirements: [
            {
              kind: 'anthropic_api_key',
              accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
              target: null,
              satisfied: state.satisfied,
              source: state.source,
            },
          ],
        })
      }
      if (url.includes('/api/env/anthropic/validate')) {
        return json({
          valid: state.valid,
          reason: state.valid ? 'ok' : 'no_key',
          model: null,
          source: state.source,
        })
      }
      if (url.includes('/api/trial')) {
        return json({ active: true, status: 'active' })
      }
      if (url.includes('/api/account/status')) {
        return json(state.account ?? { signed_in: false })
      }
      return json({})
    })
  )
}

function renderGate(client: QueryClient) {
  return renderHook(() => useAiGate(), { wrapper: queryClientWrapper(client) })
}

afterEach(() => vi.unstubAllGlobals())

describe('useAiGate', () => {
  it('unblocks after a key save even with a cached negative verdict', async () => {
    const server: ServerState = {
      source: 'missing',
      satisfied: false,
      valid: false,
    }
    serveEnv(server)
    const client = createTestQueryClient()
    // A verdict probed while no key was configured. Its staleTime outlives the
    // save, so the gate must be told to drop it rather than trust the cache.
    client.setQueryData(['anthropic-validity'], {
      valid: false,
      reason: 'no_key',
      model: null,
      source: 'none',
    })

    const { result } = renderGate(client)
    await waitFor(() =>
      expect(result.current).toEqual({ status: 'blocked', reason: 'missing' })
    )

    server.source = 'process_env'
    server.satisfied = true
    server.valid = true
    await invalidateTrialRelatedQueries(client)

    await waitFor(() => expect(result.current).toEqual({ status: 'ready' }))
  })

  it('keeps legacy trial tokens blocked after Readyset sign-out', async () => {
    const server: ServerState = {
      source: 'missing',
      satisfied: false,
      valid: false,
    }
    serveEnv(server)
    const client = createTestQueryClient()

    const { result } = renderGate(client)
    await waitFor(() =>
      expect(result.current).toEqual({ status: 'blocked', reason: 'missing' })
    )

    server.source = 'trial'
    server.satisfied = true
    server.valid = true
    await invalidateTrialRelatedQueries(client)

    await waitFor(() =>
      expect(result.current).toEqual({ status: 'blocked', reason: 'missing' })
    )
  })

  describe('hosted AI without an account', () => {
    const anonymous = (signin_prompt: string) => ({
      signed_in: false,
      is_anonymous: true,
      signin_prompt,
      percent_used: 10,
      days_remaining: 20,
      trial_days: 30,
    })

    it.each([
      'none',
      'soft',
      'budget',
    ])('counts the free allowance as AI access while the prompt is %s', async (prompt) => {
      serveEnv({
        source: 'missing',
        satisfied: false,
        valid: false,
        account: anonymous(prompt),
      })
      const { result } = renderGate(createTestQueryClient())
      await waitFor(() => expect(result.current).toEqual({ status: 'ready' }))
    })

    it('blocks on a sign-in once the allowance has ended', async () => {
      serveEnv({
        source: 'missing',
        satisfied: false,
        valid: false,
        account: anonymous('required'),
      })
      const { result } = renderGate(createTestQueryClient())
      await waitFor(() =>
        expect(result.current).toEqual({
          status: 'blocked',
          reason: 'signin_required',
        })
      )
    })

    it('blocks an anonymous session the backend reports as hosted AI, once it has ended', async () => {
      serveEnv({
        source: 'readyset_account',
        satisfied: true,
        valid: true,
        account: anonymous('required'),
      })
      const { result } = renderGate(createTestQueryClient())
      await waitFor(() =>
        expect(result.current).toEqual({
          status: 'blocked',
          reason: 'signin_required',
        })
      )
    })

    it('leaves an own key alone whatever the prompt says', async () => {
      serveEnv({
        source: 'process_env',
        satisfied: true,
        valid: true,
        account: anonymous('required'),
      })
      const { result } = renderGate(createTestQueryClient())
      await waitFor(() => expect(result.current).toEqual({ status: 'ready' }))
    })

    it('keeps a signed-in account ready', async () => {
      serveEnv({
        source: 'readyset_account',
        satisfied: true,
        valid: true,
        account: {
          signed_in: true,
          is_anonymous: false,
          signin_prompt: 'none',
          email: 'person@example.com',
        },
      })
      const { result } = renderGate(createTestQueryClient())
      await waitFor(() => expect(result.current).toEqual({ status: 'ready' }))
    })
  })
})
