import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const appDir = resolve(__dirname, '..')
const distDir = resolve(appDir, 'dist')

function findBuiltApp() {
  for (const entry of readdirSync(distDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || !entry.name.startsWith('mac')) continue
    const appPath = join(distDir, entry.name, 'RDST Desktop.app')
    if (existsSync(appPath) && statSync(appPath).isDirectory()) {
      return appPath
    }
  }
  return null
}

function assertNodeBinary(directoryPath) {
  if (!existsSync(directoryPath)) {
    throw new Error(`Missing expected prebuild directory: ${directoryPath}`)
  }

  const hasBinary = readdirSync(directoryPath).some((name) =>
    name.endsWith('.node')
  )
  if (!hasBinary) {
    throw new Error(`Expected at least one .node binary in ${directoryPath}`)
  }
}

function getAsarEntries(appAsarPath) {
  const pnpmCmd = process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm'
  const result = spawnSync(pnpmCmd, ['exec', 'asar', 'list', appAsarPath], {
    cwd: appDir,
    encoding: 'utf8',
  })

  if (result.status !== 0) {
    throw new Error(
      result.stderr || result.stdout || `Failed to list ${appAsarPath}`
    )
  }

  return result.stdout.split('\n')
}

function assertAsarEntry(entries, expectedEntry) {
  if (!entries.includes(expectedEntry)) {
    throw new Error(`Missing expected app.asar entry: ${expectedEntry}`)
  }
}

const explicitAppPath = process.argv[2]
  ? resolve(appDir, process.argv[2])
  : null
const appPath = explicitAppPath ?? findBuiltApp()

if (!appPath) {
  throw new Error('Unable to locate a packaged RDST Desktop.app under dist/mac*')
}

const resourcesPath = join(appPath, 'Contents', 'Resources')
const appAsarPath = join(resourcesPath, 'app.asar')
const appAsarUnpackedPath = join(resourcesPath, 'app.asar.unpacked')
const appAsarEntries = getAsarEntries(appAsarPath)

assertAsarEntry(
  appAsarEntries,
  '/node_modules/electron-liquid-glass/dist/index.cjs'
)
assertAsarEntry(appAsarEntries, '/node_modules/node-gyp-build/node-gyp-build.js')
assertNodeBinary(
  join(
    appAsarUnpackedPath,
    'node_modules',
    'electron-liquid-glass',
    'prebuilds',
    'darwin-arm64'
  )
)
assertNodeBinary(
  join(
    appAsarUnpackedPath,
    'node_modules',
    'electron-liquid-glass',
    'prebuilds',
    'darwin-x64'
  )
)

console.log(
  `[check-native-glass-bundle] verified packaged glass dependencies in ${appPath}`
)
