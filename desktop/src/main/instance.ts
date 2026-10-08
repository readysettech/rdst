import os from 'node:os'
import path from 'node:path'

import flavors from '../../flavors.json' with { type: 'json' }

// A packaged build's identity. Production has no instance; the staging variant and Gerrit
// change builds each get their own app id, URL scheme and RDST data so they can be installed
// next to production. electron-builder bakes the instance into package.json as rdstInstance.

type Flavor = (typeof flavors)[keyof typeof flavors]

const BY_INSTANCE = new Map<string, Flavor>(
  Object.values(flavors).map((flavor) => [flavor.instance, flavor])
)

/** '' is production. */
export type Instance = string

const flavorOf = (instance: Instance): Flavor =>
  BY_INSTANCE.get(instance) ?? flavors.release

/** The packaged rdstInstance; anything other than a known instance is production. */
export function parseInstance(value: unknown): Instance {
  return typeof value === 'string' && BY_INSTANCE.has(value) ? value : ''
}

/** The OAuth return scheme; unpackaged development always uses rdst-dev. */
export function urlScheme(instance: Instance, packaged: boolean): string {
  return packaged ? flavorOf(instance).scheme : 'rdst-dev'
}

/** The electron-builder appId, which is also the Windows AppUserModelID. */
export function appId(instance: Instance): string {
  return flavorOf(instance).appId
}

export function productName(instance: Instance): string {
  return flavorOf(instance).productName
}

/** The flavor's name, which names its packages and update channel. */
export function packageName(instance: Instance): string {
  return flavorOf(instance).name
}

/** The sidecar's data directory: RDST_DATA_DIR wins, then ~/.rdst-<instance>, then ~/.rdst. */
export function dataDir(
  instance: Instance,
  env: NodeJS.ProcessEnv = process.env,
  homeDir: string = os.homedir()
): string {
  const configured = env.RDST_DATA_DIR?.trim()
  if (configured) {
    return /^~(?=$|[\\/])/.test(configured)
      ? path.join(homeDir, configured.slice(1))
      : configured
  }
  return path.join(homeDir, instance ? `.rdst-${instance}` : '.rdst')
}
