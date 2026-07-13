import { describe, expect, it } from 'vitest'

import {
  isNewerVersion,
  manualDownloadLinks,
  parseUpdateMetadata,
  resolveUpdateMode,
} from './update-policy.js'

describe('resolveUpdateMode', () => {
  it('disables updates for unpackaged builds', () => {
    expect(
      resolveUpdateMode({
        isPackaged: false,
        updatesEnabled: true,
        platform: 'linux',
        appImagePath: '/tmp/rdst.AppImage',
      })
    ).toBe('disabled')
  })

  it('disables updates for preview builds without the baked flag', () => {
    expect(
      resolveUpdateMode({
        isPackaged: true,
        updatesEnabled: false,
        platform: 'linux',
        appImagePath: '/tmp/rdst.AppImage',
      })
    ).toBe('disabled')
  })

  it('updates AppImage installs in place', () => {
    expect(
      resolveUpdateMode({
        isPackaged: true,
        updatesEnabled: true,
        platform: 'linux',
        appImagePath: '/tmp/rdst.AppImage',
      })
    ).toBe('auto')
  })

  it('notifies deb and rpm installs', () => {
    expect(
      resolveUpdateMode({
        isPackaged: true,
        updatesEnabled: true,
        platform: 'linux',
        appImagePath: undefined,
      })
    ).toBe('notify')
  })

  it('notifies macOS installs', () => {
    expect(
      resolveUpdateMode({
        isPackaged: true,
        updatesEnabled: true,
        platform: 'darwin',
        appImagePath: undefined,
      })
    ).toBe('notify')
  })

  it('disables updates on unsupported platforms', () => {
    expect(
      resolveUpdateMode({
        isPackaged: true,
        updatesEnabled: true,
        platform: 'win32',
        appImagePath: undefined,
      })
    ).toBe('disabled')
  })
})

describe('parseUpdateMetadata', () => {
  it('extracts the version and file names from channel metadata', () => {
    const yml = [
      'version: 1.0.42',
      'files:',
      '  - url: rdst-desktop-1.0.42-arm64.zip',
      '    sha512: abc',
      '    size: 4',
      '  - url: rdst-desktop-1.0.42-arm64.dmg',
      '    sha512: def',
      'path: rdst-desktop-1.0.42-arm64.zip',
      'sha512: abc',
      "releaseDate: '2026-07-13T00:00:00.000Z'",
    ].join('\n')
    expect(parseUpdateMetadata(yml)).toEqual({
      version: '1.0.42',
      files: [
        { url: 'rdst-desktop-1.0.42-arm64.zip' },
        { url: 'rdst-desktop-1.0.42-arm64.dmg' },
      ],
    })
  })

  it('returns a null version for unparseable metadata', () => {
    expect(parseUpdateMetadata('not metadata')).toEqual({
      version: null,
      files: [],
    })
  })
})

describe('isNewerVersion', () => {
  it('compares build numbers numerically', () => {
    expect(isNewerVersion('1.0.99', '1.0.100')).toBe(true)
    expect(isNewerVersion('1.0.100', '1.0.99')).toBe(false)
    expect(isNewerVersion('1.0.100', '1.0.100')).toBe(false)
  })

  it('gives major and minor components precedence', () => {
    expect(isNewerVersion('1.0.500', '1.1.2')).toBe(true)
    expect(isNewerVersion('2.0.1', '1.9.900')).toBe(false)
  })

  it('treats missing components as zero', () => {
    expect(isNewerVersion('1.0', '1.0.1')).toBe(true)
    expect(isNewerVersion('1.0.0', '1.0')).toBe(false)
  })
})

describe('manualDownloadLinks', () => {
  it('links the dmg referenced by the update metadata on macOS', () => {
    expect(
      manualDownloadLinks({
        platform: 'darwin',
        arch: 'arm64',
        version: '1.0.42',
        files: [
          { url: 'rdst-desktop-1.0.42-arm64.zip' },
          { url: 'rdst-desktop-1.0.42-arm64.dmg' },
        ],
      })
    ).toEqual([
      {
        label: '.dmg',
        url: 'https://downloads.readyset.io/packages/rdst-desktop/macos/update/rdst-desktop-1.0.42-arm64.dmg',
      },
    ])
  })

  it('constructs the dmg name when the metadata omits it', () => {
    expect(
      manualDownloadLinks({
        platform: 'darwin',
        arch: 'arm64',
        version: '1.0.42',
        files: [{ url: 'rdst-desktop-1.0.42-arm64.zip' }],
      })
    ).toEqual([
      {
        label: '.dmg',
        url: 'https://downloads.readyset.io/packages/rdst-desktop/macos/update/rdst-desktop-1.0.42-arm64.dmg',
      },
    ])
  })

  it('constructs deb and rpm links on Linux', () => {
    expect(
      manualDownloadLinks({
        platform: 'linux',
        arch: 'x64',
        version: '1.0.42',
        files: [{ url: 'rdst-desktop-1.0.42-x86_64.AppImage' }],
      })
    ).toEqual([
      {
        label: '.deb',
        url: 'https://downloads.readyset.io/packages/rdst-desktop/linux/update/rdst-desktop-1.0.42-amd64.deb',
      },
      {
        label: '.rpm',
        url: 'https://downloads.readyset.io/packages/rdst-desktop/linux/update/rdst-desktop-1.0.42-x86_64.rpm',
      },
    ])
  })

  it('returns no links on unsupported platforms', () => {
    expect(
      manualDownloadLinks({
        platform: 'win32',
        arch: 'x64',
        version: '1.0.42',
        files: [],
      })
    ).toEqual([])
  })
})
