import { describe, expect, it } from 'vitest'

import { trayBitmap } from './tray-icons.js'

function solid(width: number, height: number, bgra: [number, number, number, number]): Buffer {
  const data = Buffer.alloc(width * height * 4)
  for (let offset = 0; offset < data.length; offset += 4) data.set(bgra, offset)
  return data
}

function pixel(data: Buffer, width: number, x: number, y: number): number[] {
  const offset = (y * width + x) * 4
  return [...data.subarray(offset, offset + 4)]
}

describe('tray icon variants', () => {
  const base = { data: solid(16, 16, [200, 100, 20, 255]), width: 16, height: 16, template: false }

  it('leaves the running icon as it is', () => {
    expect(trayBitmap(base, 'running').data.equals(base.data)).toBe(true)
  })

  it('draws a red dot in the corner for a problem', () => {
    const drawn = trayBitmap(base, 'problem')
    expect(pixel(drawn.data, 16, 13, 13)).toEqual([0x4d, 0x48, 0xe5, 255])
    expect(pixel(drawn.data, 16, 1, 1)).toEqual([200, 100, 20, 255])
    expect(drawn.template).toBe(false)
  })

  it('turns a macOS template glyph grey so the colored dot can show', () => {
    const template = { ...base, data: solid(16, 16, [0, 0, 0, 255]), template: true }
    const drawn = trayBitmap(template, 'problem')
    expect(drawn.template).toBe(false)
    expect(pixel(drawn.data, 16, 1, 1)).toEqual([0x8e, 0x8e, 0x8e, 255])
  })

  it('fades to grey when paused or offline', () => {
    for (const state of ['paused', 'offline'] as const) {
      const [b, g, r, a] = pixel(trayBitmap(base, state).data, 16, 4, 4)
      expect(b).toBe(g)
      expect(g).toBe(r)
      expect(a).toBe(128)
    }
    expect(base.data[3]).toBe(255)
  })
})
