import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { appId, productName } from './instance.js'

export interface LoginStatus {
  enabled: boolean
  available: boolean
  note?: string
}

interface LoginApp {
  getLoginItemSettings: (options?: { args?: string[] }) => { openAtLogin: boolean }
  setLoginItemSettings: (settings: {
    openAtLogin: boolean
    openAsHidden: boolean
    args: string[]
  }) => void
}

/** Names the Linux autostart entry: its file is `<appId>.desktop`. */
export interface LoginIdentity {
  appId: string
  productName: string
}

const PRODUCTION_IDENTITY: LoginIdentity = {
  appId: appId(''),
  productName: productName(''),
}
const HIDDEN_ARG = '--hidden'

function autostartPath(
  env: NodeJS.ProcessEnv,
  identity: LoginIdentity
): string {
  return path.join(
    env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'),
    'autostart',
    `${identity.appId}.desktop`
  )
}

function escapeDesktopExec(value: string): string {
  return `"${value.replace(/[%"\\$`]/g, (character) => character === '%' ? '%%' : `\\${character}`)}"`
}

export function getStartAtLogin(
  app: LoginApp,
  platform: NodeJS.Platform = process.platform,
  env: NodeJS.ProcessEnv = process.env,
  identity: LoginIdentity = PRODUCTION_IDENTITY
): LoginStatus {
  if (platform === 'linux') {
    if (env.APPIMAGE) {
      return { enabled: false, available: false, note: 'Start at login is unavailable in AppImage builds.' }
    }
    return { enabled: fs.existsSync(autostartPath(env, identity)), available: true }
  }
  if (platform === 'darwin' || platform === 'win32') {
    return { enabled: app.getLoginItemSettings({ args: [HIDDEN_ARG] }).openAtLogin, available: true }
  }
  return { enabled: false, available: false, note: 'Start at login is unavailable on this platform.' }
}

export function setStartAtLogin(
  app: LoginApp,
  enabled: boolean,
  platform: NodeJS.Platform = process.platform,
  env: NodeJS.ProcessEnv = process.env,
  executable: string = process.execPath,
  appArgs: string[] = [],
  identity: LoginIdentity = PRODUCTION_IDENTITY
): LoginStatus {
  if (platform === 'linux') {
    if (env.APPIMAGE) return getStartAtLogin(app, platform, env, identity)
    const entry = autostartPath(env, identity)
    if (enabled) {
      fs.mkdirSync(path.dirname(entry), { recursive: true })
      fs.writeFileSync(entry, [
        '[Desktop Entry]',
        'Type=Application',
        `Name=${identity.productName}`,
        `Exec=${[executable, ...appArgs].map(escapeDesktopExec).join(' ')} ${HIDDEN_ARG}`,
        'Terminal=false',
        'X-GNOME-Autostart-enabled=true',
        '',
      ].join('\n'), 'utf8')
    } else {
      fs.rmSync(entry, { force: true })
    }
    return getStartAtLogin(app, platform, env, identity)
  }
  if (platform === 'darwin' || platform === 'win32') {
    app.setLoginItemSettings({ openAtLogin: enabled, openAsHidden: true, args: [HIDDEN_ARG] })
  }
  return getStartAtLogin(app, platform, env, identity)
}

export function isHiddenLaunch(argv: string[] = process.argv): boolean {
  return argv.includes(HIDDEN_ARG)
}
