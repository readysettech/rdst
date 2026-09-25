import { resolve } from 'node:path'

/**
 * How a browser suite builds the app it serves. The suites run under a temporary HOME, so the
 * build calls the workspace's Vite binary directly: a package manager started there would check
 * the install against a store that doesn't exist. With RDST_E2E_DIST_DIR set, Vite builds into
 * that directory instead of dist/, so a local run never replaces the bundle a running app serves.
 */
export function webBuild(appDir: string): { distDir: string; command: string } {
  const vite = `"${resolve(appDir, 'node_modules', '.bin', 'vite')}" build`
  const custom = process.env.RDST_E2E_DIST_DIR
  if (!custom) return { distDir: resolve(appDir, 'dist'), command: vite }
  const distDir = resolve(custom)
  return { distDir, command: `${vite} --outDir "${distDir}" --emptyOutDir` }
}
