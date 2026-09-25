import { describe, expect, it, vi } from 'vitest'

import type { BackendHandle, StartupPhase } from './backend.js'
import {
  BackendStartCancelled, type BackendStartState, startupTooltip, superviseBackendStart,
} from './backend-supervisor.js'
import { startupPageHtml } from './startup-page.js'

const handle = { apiBaseUrl: 'http://127.0.0.1:1' } as BackendHandle

type Attempt = (onPhase: (phase: StartupPhase, store?: string) => void) => Promise<BackendHandle>

function run(attempts: Attempt[], cancelled = () => false) {
  const states: BackendStartState[] = []
  const sleeps: number[] = []
  const start = vi.fn(({ onPhase }) => {
    const attempt = attempts.shift()
    if (!attempt) throw new Error('no more attempts')
    return attempt(onPhase)
  })
  const result = superviseBackendStart({
    start,
    onState: (state) => states.push(state),
    sleep: async (ms) => { sleeps.push(ms) },
    retryDelaysMs: [10, 20],
    cancelled,
  })
  return { result, states, sleeps, start }
}

describe('superviseBackendStart', () => {
  it('waits out a slow start without retrying', async () => {
    const { result, states, start } = run([
      () => new Promise((resolve) => setTimeout(() => resolve(handle), 50)),
    ])
    await expect(result).resolves.toBe(handle)
    expect(start).toHaveBeenCalledTimes(1)
    expect(states.map((state) => state.phase)).toEqual(['starting', 'ready'])
  })

  it('reports a migration and goes back to starting when it finishes', async () => {
    const { result, states } = run([
      async (onPhase) => {
        onPhase('migrating', 'library.db')
        onPhase('starting')
        return handle
      },
    ])
    await result
    expect(states).toEqual([
      { phase: 'starting', attempt: 1 },
      { phase: 'migrating', attempt: 1, store: 'library.db' },
      { phase: 'starting', attempt: 1 },
      { phase: 'ready', attempt: 1 },
    ])
    expect(startupTooltip(states[1]!)).toBe('RDST - upgrading your data, this can take a minute')
  })

  it('retries a crashed start with backoff', async () => {
    const { result, states, sleeps } = run([
      async () => { throw new Error('RDST backend exited before it became ready (code=1)') },
      async () => handle,
    ])
    await expect(result).resolves.toBe(handle)
    expect(sleeps).toEqual([10])
    expect(states.map((state) => state.phase)).toEqual(['starting', 'retrying', 'starting', 'ready'])
    expect(states[1]).toMatchObject({ attempt: 1, retryInMs: 10, error: expect.stringContaining('code=1') })
  })

  it('gives up after the last retry and says why', async () => {
    const crash: Attempt = async () => { throw new Error('exited (code=3)') }
    const { result, states, sleeps } = run([crash, crash, crash])
    await expect(result).rejects.toThrow('exited (code=3)')
    expect(sleeps).toEqual([10, 20])
    expect(states.at(-1)).toEqual({ phase: 'failed', attempt: 3, error: 'exited (code=3)' })
    expect(startupTooltip(states.at(-1)!)).toBe("RDST - couldn't start its background service")
  })

  it('stops retrying once the app is quitting', async () => {
    let quitting = false
    const { result, start } = run([
      async () => { quitting = true; throw new Error('killed by quit') },
      async () => handle,
    ], () => quitting)
    await expect(result).rejects.toBeInstanceOf(BackendStartCancelled)
    expect(start).toHaveBeenCalledTimes(1)
  })
})

describe('startup page', () => {
  it('tells the person a data upgrade is running', () => {
    const html = startupPageHtml({ phase: 'migrating', attempt: 1 })
    expect(html).toContain('Upgrading your data, this can take a minute')
    expect(html).not.toContain('id="logs"')
  })

  it('offers the log and a retry after a failed start, with the error escaped', () => {
    const html = startupPageHtml({ phase: 'failed', attempt: 4, error: 'bad <thing> & more' })
    expect(html).toContain('id="logs"')
    expect(html).toContain('id="retry"')
    expect(html).toContain('bad &#60;thing&#62; &#38; more')
  })
})
