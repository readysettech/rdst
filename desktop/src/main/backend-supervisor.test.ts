import type { ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'

import type { BackendHandle, StartupPhase } from './backend.js'
import {
  type BackendRunState,
  BackendStartCancelled,
  type BackendStartState,
  keepBackendRunning,
  restartDelayMs,
  runStateTooltip,
  startupTooltip,
  superviseBackendStart,
} from './backend-supervisor.js'
import { startupPageHtml } from './startup-page.js'

const handle = { apiBaseUrl: 'http://127.0.0.1:1' } as BackendHandle

type Attempt = (
  onPhase: (phase: StartupPhase, store?: string) => void
) => Promise<BackendHandle>

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
    sleep: async (ms) => {
      sleeps.push(ms)
    },
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
    expect(startupTooltip(states[1]!)).toBe(
      'RDST - upgrading your data, this can take a minute'
    )
  })

  it('retries a crashed start with backoff', async () => {
    const { result, states, sleeps } = run([
      async () => {
        throw new Error('RDST backend exited before it became ready (code=1)')
      },
      async () => handle,
    ])
    await expect(result).resolves.toBe(handle)
    expect(sleeps).toEqual([10])
    expect(states.map((state) => state.phase)).toEqual([
      'starting',
      'retrying',
      'starting',
      'ready',
    ])
    expect(states[1]).toMatchObject({
      attempt: 1,
      retryInMs: 10,
      error: expect.stringContaining('code=1'),
    })
  })

  it('gives up after the last retry and says why', async () => {
    const crash: Attempt = async () => {
      throw new Error('exited (code=3)')
    }
    const { result, states, sleeps } = run([crash, crash, crash])
    await expect(result).rejects.toThrow('exited (code=3)')
    expect(sleeps).toEqual([10, 20])
    expect(states.at(-1)).toEqual({
      phase: 'failed',
      attempt: 3,
      error: 'exited (code=3)',
    })
    expect(startupTooltip(states.at(-1)!)).toBe(
      "RDST - couldn't start its background service"
    )
  })

  it('stops retrying once the app is quitting', async () => {
    let quitting = false
    const { result, start } = run(
      [
        async () => {
          quitting = true
          throw new Error('killed by quit')
        },
        async () => handle,
      ],
      () => quitting
    )
    await expect(result).rejects.toBeInstanceOf(BackendStartCancelled)
    expect(start).toHaveBeenCalledTimes(1)
  })
})

function fakeBackend(
  url: string
): BackendHandle & { exit: (code?: number) => void } {
  const process = Object.assign(new EventEmitter(), {
    exitCode: null,
    signalCode: null,
  })
  return {
    process: process as unknown as ChildProcess,
    apiBaseUrl: url,
    binaryPath: 'rdst',
    migrating: () => false,
    exit: (code = 1) => {
      Object.assign(process, { exitCode: code })
      process.emit('exit', code, null)
    },
  }
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

function keep(
  starts: Array<() => Promise<BackendHandle>>,
  opts: { quitting?: () => boolean; clock?: () => number } = {}
) {
  const states: BackendRunState[] = []
  const sleeps: number[] = []
  const restarted: string[] = []
  const stopped: string[] = []
  const exits: Array<number | null> = []
  const initial = fakeBackend('http://127.0.0.1:1')
  const keeper = keepBackendRunning(initial, {
    start: () => {
      const next = starts.shift()
      if (!next) return new Promise(() => {})
      return next()
    },
    stop: async (handle) => {
      stopped.push(handle.apiBaseUrl)
    },
    onExit: ({ code }) => exits.push(code),
    onState: (state) => states.push(state),
    onRestarted: (handle) => restarted.push(handle.apiBaseUrl),
    quitting: opts.quitting ?? (() => false),
    delaysMs: [10, 20],
    everyMs: 30,
    stableRunMs: 1_000,
    now: opts.clock,
    sleep: async (ms) => {
      sleeps.push(ms)
    },
  })
  return { initial, keeper, states, sleeps, restarted, stopped, exits }
}

describe('keepBackendRunning', () => {
  it('backs off 2, 5 and 15 seconds, then tries every 30 seconds', () => {
    expect([1, 2, 3, 4, 9].map((attempt) => restartDelayMs(attempt))).toEqual([
      2_000, 5_000, 15_000, 30_000, 30_000,
    ])
  })

  it('restarts a backend that exits and reports the new address', async () => {
    const second = fakeBackend('http://127.0.0.1:2')
    const run = keep([async () => second])
    run.initial.exit(1)
    await settle()
    expect(run.exits).toEqual([1])
    expect(run.sleeps).toEqual([10])
    expect(run.states).toEqual(['restarting', 'running'])
    expect(run.restarted).toEqual(['http://127.0.0.1:2'])
    // The restarted backend is watched too.
    second.exit(3)
    await settle()
    expect(run.exits).toEqual([1, 3])
  })

  it('says it failed once the backoff runs out and keeps trying', async () => {
    const crash = async (): Promise<BackendHandle> => {
      throw new Error('exited')
    }
    const run = keep([
      crash,
      crash,
      crash,
      async () => fakeBackend('http://127.0.0.1:4'),
    ])
    run.initial.exit(1)
    await settle()
    expect(run.sleeps).toEqual([10, 20, 30, 30])
    expect(run.states).toEqual([
      'restarting',
      'restarting',
      'failed',
      'failed',
      'running',
    ])
    expect(runStateTooltip('failed')).toContain('keeps stopping')
    expect(runStateTooltip('running')).toBeNull()
  })

  it('starts the backoff over only after a backend ran for a while', async () => {
    let time = 0
    const quick = fakeBackend('http://127.0.0.1:2')
    const steady = fakeBackend('http://127.0.0.1:3')
    const run = keep(
      [async () => quick, async () => steady, async () => fakeBackend('x')],
      {
        clock: () => time,
      }
    )
    run.initial.exit(1)
    await settle()
    quick.exit(1)
    await settle()
    expect(run.sleeps).toEqual([10, 20])
    time = 5_000
    steady.exit(1)
    await settle()
    expect(run.sleeps).toEqual([10, 20, 10])
  })

  it('leaves an exit during quit alone', async () => {
    const run = keep([async () => fakeBackend('http://127.0.0.1:2')], {
      quitting: () => true,
    })
    run.initial.exit(0)
    await settle()
    expect(run.states).toEqual([])
    expect(run.exits).toEqual([])
  })

  it('stops a backend that finished starting after the app began quitting', async () => {
    let quitting = false
    const run = keep(
      [
        async () => {
          quitting = true
          return fakeBackend('http://127.0.0.1:2')
        },
      ],
      { quitting: () => quitting }
    )
    run.initial.exit(1)
    await settle()
    expect(run.stopped).toEqual(['http://127.0.0.1:2'])
    expect(run.restarted).toEqual([])
  })

  it('stops watching when told to', async () => {
    const run = keep([async () => fakeBackend('http://127.0.0.1:2')])
    run.keeper.stop()
    run.initial.exit(1)
    await settle()
    expect(run.states).toEqual([])
  })
})

describe('startup page', () => {
  it('tells the person a data upgrade is running', () => {
    const html = startupPageHtml({ phase: 'migrating', attempt: 1 })
    expect(html).toContain('Upgrading your data, this can take a minute')
    expect(html).not.toContain('id="logs"')
  })

  it('offers the log and a retry after a failed start, with the error escaped', () => {
    const html = startupPageHtml({
      phase: 'failed',
      attempt: 4,
      error: 'bad <thing> & more',
    })
    expect(html).toContain('id="logs"')
    expect(html).toContain('id="retry"')
    expect(html).toContain('bad &#60;thing&#62; &#38; more')
  })
})
