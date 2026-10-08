import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getStartAtLogin, isHiddenLaunch, setStartAtLogin } from './login.js'

const temporary: string[] = []
afterEach(() => {
  for (const dir of temporary.splice(0)) fs.rmSync(dir, { recursive: true, force: true })
})

function fakeApp() {
  let enabled = false
  return {
    getLoginItemSettings: vi.fn(() => ({ openAtLogin: enabled })),
    setLoginItemSettings: vi.fn((value: { openAtLogin: boolean }) => { enabled = value.openAtLogin }),
  }
}

describe('start at login', () => {
  it.each(['darwin', 'win32'] as const)('sets a hidden login launch on %s', (platform) => {
    const app = fakeApp()
    expect(setStartAtLogin(app, true, platform).enabled).toBe(true)
    expect(app.setLoginItemSettings).toHaveBeenCalledWith({
      openAtLogin: true, openAsHidden: true, args: ['--hidden'],
    })
    expect(app.getLoginItemSettings).toHaveBeenCalledWith({ args: ['--hidden'] })
    expect(isHiddenLaunch(['rdst', '--hidden'])).toBe(true)
    expect(setStartAtLogin(app, false, platform).enabled).toBe(false)
  })

  it('writes and removes an XDG autostart entry with a quoted executable', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-login-'))
    temporary.push(dir)
    const env = { XDG_CONFIG_HOME: dir }
    const app = fakeApp()
    const entry = path.join(dir, 'autostart', 'com.readyset.rdst-desktop.desktop')
    expect(setStartAtLogin(app, true, 'linux', env, '/opt/RDST Desktop/rdst').enabled).toBe(true)
    expect(fs.readFileSync(entry, 'utf8')).toContain('Exec="/opt/RDST Desktop/rdst" --hidden\n')
    expect(setStartAtLogin(app, false, 'linux', env).enabled).toBe(false)
    expect(fs.existsSync(entry)).toBe(false)
    expect(app.setLoginItemSettings).not.toHaveBeenCalled()
  })

  it('includes the app path when the Linux executable is Electron in development', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-login-'))
    temporary.push(dir)
    const entry = path.join(dir, 'autostart', 'com.readyset.rdst-desktop.desktop')
    setStartAtLogin(fakeApp(), true, 'linux', { XDG_CONFIG_HOME: dir },
      '/opt/Electron/electron', ['/work/RDST Desktop'])
    expect(fs.readFileSync(entry, 'utf8')).toContain(
      'Exec="/opt/Electron/electron" "/work/RDST Desktop" --hidden\n'
    )
  })

  it('names the Linux entry after the build it starts', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-login-'))
    temporary.push(dir)
    const env = { XDG_CONFIG_HOME: dir }
    const app = fakeApp()
    const staging = {
      appId: 'com.readyset.rdst-desktop.staging',
      productName: 'RDST Desktop Staging',
    }
    const production = path.join(
      dir,
      'autostart',
      'com.readyset.rdst-desktop.desktop'
    )
    const entry = path.join(
      dir,
      'autostart',
      'com.readyset.rdst-desktop.staging.desktop'
    )
    setStartAtLogin(app, true, 'linux', env, '/opt/rdst', [])
    expect(
      setStartAtLogin(app, true, 'linux', env, '/opt/rdst', [], staging).enabled
    ).toBe(true)
    expect(fs.readFileSync(entry, 'utf8')).toContain(
      'Name=RDST Desktop Staging\n'
    )
    expect(
      setStartAtLogin(app, false, 'linux', env, '/opt/rdst', [], staging)
        .enabled
    ).toBe(false)
    expect(getStartAtLogin(app, 'linux', env, staging).enabled).toBe(false)
    expect(fs.existsSync(entry)).toBe(false)
    expect(fs.readFileSync(production, 'utf8')).toContain('Name=RDST Desktop\n')
  })

  it('explains why AppImage login launch is unavailable', () => {
    const app = fakeApp()
    const env = { APPIMAGE: '/tmp/RDST.AppImage' }
    expect(getStartAtLogin(app, 'linux', env)).toEqual({
      enabled: false, available: false,
      note: 'Start at login is unavailable in AppImage builds.',
    })
    expect(setStartAtLogin(app, true, 'linux', env).available).toBe(false)
  })
})
