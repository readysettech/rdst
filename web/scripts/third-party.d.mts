import type { Plugin } from 'vite'

export const RECORD_FILE: string

export interface LicenseText {
  file: string | null
  text: string
}

export interface Component {
  name: string
  version?: string
  license: string
  note?: string
  texts: LicenseText[]
}

export function packageComponent(dir: string): Component
export function packageDir(moduleId: string): string | null
export function bundleComponents(moduleIds: string[]): Component[]
export function assetComponents(publicDir: string, iconsDir: string): Component[]
export function thirdPartyRecord(): Plugin
