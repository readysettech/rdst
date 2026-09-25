import fs from 'node:fs'
import { defineConfig } from 'tsup'

// The main process uses the sidecar's telemetry config even when it cannot start. Builds
// outside the monorepo layout (the public mirror) have no sidecar source and ship without it.
const backendConfigUrl = new URL('../../../rdst/shared/telemetry_manager.py', import.meta.url)
const backendConfig = fs.existsSync(backendConfigUrl)
  ? fs.readFileSync(backendConfigUrl, 'utf8')
  : null
const configValue = (source: string, name: string): string => {
  const value = source.match(
    new RegExp(`${name} = (?:os\\.environ\\.get\\([^,]+, )?"([^"]*)"`)
  )?.[1]
  if (value === undefined)
    throw new Error(`Missing backend telemetry config: ${name}`)
  return value
}
const telemetryConfig = backendConfig
  ? {
      key: configValue(backendConfig, 'POSTHOG_API_KEY'),
      host: configValue(backendConfig, 'POSTHOG_HOST'),
      internalProbeUrl: configValue(backendConfig, 'INTERNAL_INSTALL_PROBE_URL'),
    }
  : { key: '', host: '', internalProbeUrl: '' }

export default defineConfig([
  {
    entry: { 'main/index': 'src/main/index.ts' },
    define: { __RDST_TELEMETRY_CONFIG__: JSON.stringify(telemetryConfig) },
    outDir: 'out',
    format: 'esm',
    platform: 'node',
    target: 'node22',
    external: ['electron', 'electron-updater'],
    clean: false,
    sourcemap: true,
  },
  {
    entry: { 'preload/index': 'src/preload/index.ts' },
    outDir: 'out',
    format: 'esm',
    outExtension: () => ({ js: '.mjs' }),
    platform: 'node',
    target: 'node22',
    external: ['electron'],
    clean: false,
    sourcemap: true,
  },
])
