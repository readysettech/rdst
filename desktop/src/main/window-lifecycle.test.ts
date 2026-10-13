import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'

import {
  cleanUpThen,
  hideWindowOnClose,
  installOrQuit,
  stopBeforeQuit,
} from './window-lifecycle.js'

describe('desktop window lifecycle', () => {
  it('hides a closed window while the backend remains running', () => {
    const window = Object.assign(new EventEmitter(), { hide: vi.fn() })
    let quitting = false
    hideWindowOnClose(window as never, () => quitting)
    const event = { preventDefault: vi.fn() }
    window.emit('close', event)
    expect(event.preventDefault).toHaveBeenCalledOnce()
    expect(window.hide).toHaveBeenCalledOnce()

    quitting = true
    window.emit('close', event)
    expect(window.hide).toHaveBeenCalledOnce()
  })

  it('waits for sidecar cleanup before quitting, once', async () => {
    let finish!: () => void
    const cleanup = vi.fn(() => new Promise<void>((resolve) => { finish = resolve }))
    const quit = vi.fn()
    const preventDefault = vi.fn()
    const state = { started: false, completed: false }
    stopBeforeQuit({ preventDefault }, state, cleanup, quit)
    stopBeforeQuit({ preventDefault }, state, cleanup, quit)
    expect(preventDefault).toHaveBeenCalledTimes(2)
    expect(cleanup).toHaveBeenCalledOnce()
    expect(quit).not.toHaveBeenCalled()
    finish()
    await vi.waitFor(() => expect(quit).toHaveBeenCalledOnce())
    stopBeforeQuit({ preventDefault }, state, cleanup, quit)
    expect(preventDefault).toHaveBeenCalledTimes(2)
  })

  it('installs an update only after cleanup, with windows closing and the quit unblocked', async () => {
    const state = { started: false, completed: false }
    const window = Object.assign(new EventEmitter(), { hide: vi.fn() })
    hideWindowOnClose(window as never, () => state.started)
    const cleanup = vi.fn(() => Promise.resolve())
    const quit = vi.fn()
    const preventDefault = vi.fn()
    const install = vi.fn(() => {
      // The updater closes every window, then quits the app.
      window.emit('close', { preventDefault })
      stopBeforeQuit({ preventDefault }, state, cleanup, quit)
    })
    cleanUpThen(state, cleanup, install)
    cleanUpThen(state, cleanup, install)
    expect(install).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(install).toHaveBeenCalledOnce())
    expect(cleanup).toHaveBeenCalledOnce()
    expect(window.hide).not.toHaveBeenCalled()
    expect(preventDefault).not.toHaveBeenCalled()
    expect(quit).not.toHaveBeenCalled()
  })

  it('quits when an install errors, returns without quitting, or throws, once', () => {
    vi.useFakeTimers()
    try {
      const errorListeners: Array<() => void> = []
      const quit = vi.fn()
      const start = (install: () => void) =>
        installOrQuit({
          install,
          onError: (listener) => errorListeners.push(listener),
          quit,
          timeoutMs: 10_000,
        })

      start(() => undefined)
      errorListeners[0]?.()
      vi.advanceTimersByTime(10_000)
      expect(quit).toHaveBeenCalledOnce()

      quit.mockClear()
      start(() => undefined)
      vi.advanceTimersByTime(9_999)
      expect(quit).not.toHaveBeenCalled()
      vi.advanceTimersByTime(1)
      expect(quit).toHaveBeenCalledOnce()

      quit.mockClear()
      start(() => {
        throw new Error('no installer')
      })
      expect(quit).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })
})
