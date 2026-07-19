import path from 'node:path'
import { describe, expect, it } from 'vitest'

import { backendExecutablePath } from './backend.js'

function entries(value: string | undefined): string[] {
  return value?.split(path.delimiter) ?? []
}

describe('backendExecutablePath', () => {
  it('makes Docker Desktop discoverable from a minimal macOS GUI PATH', () => {
    expect(
      entries(backendExecutablePath('/usr/bin:/bin', 'darwin', '/Users/test'))
    ).toEqual([
      '/usr/bin',
      '/bin',
      '/usr/local/bin',
      '/Users/test/.docker/bin',
      '/opt/homebrew/bin',
      '/opt/local/bin',
      '/Applications/Docker.app/Contents/Resources/bin',
      '/Users/test/Applications/Docker.app/Contents/Resources/bin',
    ])
  })

  it('preserves custom entries and does not duplicate standard entries', () => {
    expect(
      entries(
        backendExecutablePath(
          '/custom/bin:/usr/local/bin:/custom/bin',
          'linux',
          '/home/test'
        )
      )
    ).toEqual([
      '/custom/bin',
      '/usr/local/bin',
      '/custom/bin',
      '/usr/bin',
      '/bin',
      '/home/test/.docker/bin',
    ])
  })

  it('leaves the Windows PATH untouched', () => {
    expect(backendExecutablePath('C:\\Windows;C:\\Docker', 'win32')).toBe(
      'C:\\Windows;C:\\Docker'
    )
  })
})
