import type { QueryClient } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createTestQueryClient, queryClientWrapper } from '@/test-utils'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { useAiGate } from '../lib/useAiGate'

// The gate reads three server facts; this fixture is the server, so a test can
// flip "key was just saved" between renders the way a real save does.
type ServerState = { source: string; satisfied: boolean; valid: boolean }

function serveEnv(state: ServerState) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      if (url.includes('/api/env/requirements')) {
        return json({
          keyring_available: true,
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

  it('unblocks after a trial activation', async () => {
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

    await waitFor(() => expect(result.current).toEqual({ status: 'ready' }))
  })
})
