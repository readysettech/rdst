import type { Input } from 'electron'
import { describe, expect, it, vi } from 'vitest'

import { DEFAULT_ZOOM_FACTOR, zoomShortcuts } from './zoom.js'

function contents(level = 0) {
  return {
    getZoomLevel: vi.fn(() => level),
    setZoomLevel: vi.fn(),
    setZoomFactor: vi.fn(),
  }
}

function key(key: string, extra: Partial<Input> = {}): Input {
  return {
    type: 'keyDown',
    key,
    control: true,
    meta: false,
    alt: false,
    ...extra,
  } as Input
}

describe('zoom shortcuts', () => {
  it('steps with = and -, and returns to the shell actual size with 0', () => {
    const page = contents(-1)
    const handle = zoomShortcuts(page, false)
    const event = { preventDefault: vi.fn() }
    handle(event, key('='))
    expect(page.setZoomLevel).toHaveBeenLastCalledWith(-0.5)
    handle(event, key('-'))
    expect(page.setZoomLevel).toHaveBeenLastCalledWith(-1.5)
    handle(event, key('0'))
    expect(page.setZoomFactor).toHaveBeenCalledWith(DEFAULT_ZOOM_FACTOR)
    expect(page.setZoomLevel).toHaveBeenCalledTimes(2)
    expect(event.preventDefault).toHaveBeenCalledTimes(3)
  })

  it('leaves other keys and the other platform modifier alone', () => {
    const page = contents()
    const event = { preventDefault: vi.fn() }
    zoomShortcuts(page, true)(event, key('='))
    zoomShortcuts(page, false)(event, key('a'))
    zoomShortcuts(page, false)(event, key('=', { alt: true }))
    expect(event.preventDefault).not.toHaveBeenCalled()
    expect(page.setZoomLevel).not.toHaveBeenCalled()
  })

  it('steps a held key at most every 150 ms', () => {
    const page = contents()
    let now = 1000
    const handle = zoomShortcuts(page, false, () => now)
    const event = { preventDefault: vi.fn() }
    handle(event, key('='))
    now += 50
    handle(event, key('=', { isAutoRepeat: true }))
    expect(page.setZoomLevel).toHaveBeenCalledTimes(1)
    now += 150
    handle(event, key('=', { isAutoRepeat: true }))
    expect(page.setZoomLevel).toHaveBeenCalledTimes(2)
  })
})
