// First-run state (the tour, first-run pop-ups) belongs to one RDST installation, not to the
// browser profile: Electron keeps its storage when the data directory is reset, and a new
// data directory should see the first run again. /api/status names the installation.

let installation: string | null = null

/** Remember the installation /api/status named; null keeps the one already known. */
export function setInstallationScope(id: string | null | undefined): void {
  if (id) installation = id
}

/** `base` keyed to this installation; `base:local` until the installation is known. */
export function scopedKey(base: string): string {
  return `${base}:${installation ?? 'local'}`
}
