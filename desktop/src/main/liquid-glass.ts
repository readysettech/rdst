export type GlassModule = {
  isGlassSupported?: () => boolean
  addView?: (
    handle: Buffer,
    options: { cornerRadius: number; tintColor: string; opaque: boolean }
  ) => number
  unstable_setSubdued?: (id: number, value: number) => void
  unstable_setScrim?: (id: number, value: number) => void
}

export interface LoadLiquidGlassOptions {
  platform: NodeJS.Platform
  importModule?: () => Promise<{ default?: GlassModule } | GlassModule>
}

export interface LoadLiquidGlassResult {
  module: GlassModule | null
  failureReason: string | null
  source: 'package' | null
}

function formatError(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

export async function loadLiquidGlass({
  platform,
  importModule = () => import('electron-liquid-glass'),
}: LoadLiquidGlassOptions): Promise<LoadLiquidGlassResult> {
  if (platform !== 'darwin') {
    return {
      module: null,
      failureReason: null,
      source: null,
    }
  }

  try {
    const importedModule = await importModule()
    const module =
      (importedModule as { default?: GlassModule }).default ??
      (importedModule as GlassModule)

    return {
      module,
      failureReason: null,
      source: 'package',
    }
  } catch (error) {
    return {
      module: null,
      failureReason: `Unable to load electron-liquid-glass from packaged node_modules: ${formatError(error)}`,
      source: 'package',
    }
  }
}

export function createGlassFallbackLogger(
  platform: NodeJS.Platform,
  warn: (message: string) => void = console.warn
) {
  let logged = false

  return (reason: string) => {
    if (logged || platform !== 'darwin') return
    logged = true
    warn(`[liquid-glass] Falling back to Electron vibrancy: ${reason}`)
  }
}
