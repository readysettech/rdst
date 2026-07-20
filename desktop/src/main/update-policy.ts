export type UpdateMode = 'auto' | 'notify' | 'disabled'

export interface UpdateDownloadLink {
  label: string
  url: string
}

export interface UpdateStatePayload {
  status: 'available' | 'downloading' | 'ready'
  version: string
  downloadLinks: UpdateDownloadLink[]
  progress?: number
}

// Must match the per-platform publish URLs in electron-builder.yml. Only
// main-branch CI builds populate these prefixes.
export const UPDATE_FEED_URLS: Partial<Record<NodeJS.Platform, string>> = {
  darwin: 'https://downloads.readyset.io/packages/rdst-desktop/macos/update',
  linux: 'https://downloads.readyset.io/packages/rdst-desktop/linux/update',
}

export interface UpdateEnvironment {
  isPackaged: boolean
  /**
   * Baked into the packaged app's metadata at build time; true only for
   * merged main builds. Gerrit change builds are developer previews and
   * stay frozen at the version they were built from.
   */
  updatesEnabled: boolean
  platform: NodeJS.Platform
  appImagePath: string | undefined
}

/**
 * Picks how updates are delivered for this install.
 *
 * - macOS and AppImage installs update in place through electron-updater.
 * - deb/rpm installs get a notification with download links.
 */
export function resolveUpdateMode(env: UpdateEnvironment): UpdateMode {
  if (!env.isPackaged || !env.updatesEnabled) return 'disabled'
  switch (env.platform) {
    case 'linux':
      return env.appImagePath ? 'auto' : 'notify'
    case 'darwin':
      return 'auto'
    default:
      return 'disabled'
  }
}

/**
 * Extracts the version and referenced file names from electron-builder
 * channel metadata (latest-*.yml). Notification-only updates fetch and
 * parse the metadata directly because electron-updater's Linux updater
 * refuses to check for updates outside an AppImage.
 */
export function parseUpdateMetadata(yml: string): {
  version: string | null
  files: Array<{ url: string }>
} {
  const version = /^version:[ \t]*(\S+)/m.exec(yml)?.[1] ?? null
  const files = [...yml.matchAll(/^[ \t]*-[ \t]+url:[ \t]*(\S+)/gm)].flatMap(
    (match) => (match[1] ? [{ url: match[1] }] : [])
  )
  return { version, files }
}

/**
 * Compares dotted numeric versions (MAJOR.MINOR.BUILD); missing parts
 * count as zero. The build component is a monotonic CI build number, so
 * numeric comparison is a total order over published versions.
 */
export function isNewerVersion(current: string, candidate: string): boolean {
  const parse = (version: string) =>
    version.split('.').map((part) => Number.parseInt(part, 10) || 0)
  const currentParts = parse(current)
  const candidateParts = parse(candidate)
  const length = Math.max(currentParts.length, candidateParts.length)
  for (let i = 0; i < length; i++) {
    const a = candidateParts[i] ?? 0
    const b = currentParts[i] ?? 0
    if (a !== b) return a > b
  }
  return false
}

/**
 * Download links surfaced by notification-only updates. Linux package names
 * are constructed rather than read from the update metadata because
 * latest-linux.yml only references the AppImage; CI stages deb and rpm
 * packages in the update channel under these normalized names.
 */
export function manualDownloadLinks(options: {
  platform: NodeJS.Platform
  arch: string
  version: string
  files: Array<{ url: string }>
}): UpdateDownloadLink[] {
  const feedUrl = UPDATE_FEED_URLS[options.platform]
  if (!feedUrl) return []

  if (options.platform === 'darwin') {
    const dmg =
      options.files.find((file) => file.url.endsWith('.dmg'))?.url ??
      `rdst-desktop-${options.version}-${options.arch}.dmg`
    return [{ label: '.dmg', url: `${feedUrl}/${dmg}` }]
  }

  return [
    {
      label: '.deb',
      url: `${feedUrl}/rdst-desktop-${options.version}-amd64.deb`,
    },
    {
      label: '.rpm',
      url: `${feedUrl}/rdst-desktop-${options.version}-x86_64.rpm`,
    },
  ]
}
