import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

import { DesktopSettings } from './desktop-settings.js'

const temporary: string[] = []
afterEach(() => {
  for (const dir of temporary.splice(0)) fs.rmSync(dir, { recursive: true, force: true })
})

describe('desktop settings', () => {
  it('counts the runs that showed the window up to the second, across restarts', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-settings-'))
    temporary.push(dir)
    const first = new DesktopSettings(dir)
    expect(first.windowOpens).toBe(0)
    first.recordWindowOpen()
    expect(new DesktopSettings(dir).windowOpens).toBe(1)
    const second = new DesktopSettings(dir)
    second.recordWindowOpen()
    second.recordWindowOpen()
    expect(new DesktopSettings(dir).windowOpens).toBe(2)
  })

  it('reads a settings file from before the count as no opens', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-settings-'))
    temporary.push(dir)
    fs.writeFileSync(path.join(dir, 'desktop-settings.json'), '{"startAtLoginConfigured":true}')
    const settings = new DesktopSettings(dir)
    expect(settings.windowOpens).toBe(0)
    expect(settings.startAtLoginConfigured).toBe(true)
  })
})
