import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'

import { hideWindowOnClose, stopBeforeQuit } from './window-lifecycle.js'

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
})
