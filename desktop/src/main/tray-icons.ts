// Tray icon variants drawn from the base icon's bitmap (4 bytes a pixel, BGRA as Electron's
// nativeImage.toBitmap returns it): a red dot for a problem, and a faded grey icon while
// monitoring is paused or the backend is stopped.
import type { TrayState } from './tray-state.js'

export interface TrayBitmap {
  data: Buffer
  width: number
  height: number
  /** macOS template images take their color from the menu bar. */
  template: boolean
}

const RED = { b: 0x4d, g: 0x48, r: 0xe5 }

function drawDot(data: Buffer, width: number, height: number): void {
  const radius = Math.max(2, Math.round(Math.min(width, height) * 0.22))
  const cx = width - radius - 0.5
  const cy = height - radius - 0.5
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const distance = Math.hypot(x + 0.5 - (cx + 0.5), y + 0.5 - (cy + 0.5))
      if (distance > radius + 1) continue
      const offset = (y * width + x) * 4
      if (distance <= radius) {
        data[offset] = RED.b
        data[offset + 1] = RED.g
        data[offset + 2] = RED.r
        data[offset + 3] = 0xff
      } else {
        // A clear ring keeps the dot readable on top of the glyph.
        data[offset + 3] = 0
      }
    }
  }
}

export function trayBitmap(base: TrayBitmap, state: TrayState): TrayBitmap {
  const { width, height } = base
  if (base.data.length < width * height * 4) throw new Error('Tray bitmap is smaller than its size')
  if (state === 'running') return { ...base, data: Buffer.from(base.data) }
  const data = Buffer.from(base.data)
  if (state === 'problem') {
    if (base.template) {
      // A template glyph is black; a colored dot needs a non-template image, so the glyph
      // turns mid grey to stay visible on light and dark menu bars.
      for (let offset = 0; offset < data.length; offset += 4) {
        data[offset] = 0x8e
        data[offset + 1] = 0x8e
        data[offset + 2] = 0x8e
      }
    }
    drawDot(data, width, height)
    return { data, width, height, template: false }
  }
  for (let offset = 0; offset < data.length; offset += 4) {
    const luma = Math.round(0.114 * data[offset]! + 0.587 * data[offset + 1]! + 0.299 * data[offset + 2]!)
    data[offset] = luma
    data[offset + 1] = luma
    data[offset + 2] = luma
    data[offset + 3] = Math.round(data[offset + 3]! * 0.5)
  }
  return { data, width, height, template: base.template }
}
