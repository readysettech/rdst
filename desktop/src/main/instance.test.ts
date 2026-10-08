import path from 'node:path'
import { describe, expect, it } from 'vitest'

import {
  appId,
  dataDir,
  parseInstance,
  productName,
  urlScheme,
} from './instance.js'

describe('instance', () => {
  it('accepts only the known instances', () => {
    expect(parseInstance('staging')).toBe('staging')
    expect(parseInstance('cl')).toBe('cl')
    for (const value of [undefined, null, '', 'prod', 'Staging', ' cl', 1, {}])
      expect(parseInstance(value)).toBe('')
  })

  it.each([
    ['', 'rdst', 'com.readyset.rdst-desktop', 'RDST Desktop'],
    [
      'staging',
      'rdst-staging',
      'com.readyset.rdst-desktop.staging',
      'RDST Desktop Staging',
    ],
    ['cl', 'rdst-cl', 'com.readyset.rdst-desktop.cl', 'RDST Desktop CL'],
  ] as const)('names the %j build', (instance, scheme, id, name) => {
    expect(urlScheme(instance, true)).toBe(scheme)
    expect(appId(instance)).toBe(id)
    expect(productName(instance)).toBe(name)
  })

  it('keeps unpackaged development on rdst-dev', () => {
    expect(urlScheme('', false)).toBe('rdst-dev')
    expect(urlScheme('staging', false)).toBe('rdst-dev')
  })

  it('separates each instance data directory unless RDST_DATA_DIR is set', () => {
    const home = path.join(path.sep, 'home', 'me')
    expect(dataDir('', {}, home)).toBe(path.join(home, '.rdst'))
    expect(dataDir('staging', {}, home)).toBe(path.join(home, '.rdst-staging'))
    expect(dataDir('cl', { RDST_DATA_DIR: '  ' }, home)).toBe(
      path.join(home, '.rdst-cl')
    )
    expect(dataDir('cl', { RDST_DATA_DIR: ' /data/rdst ' }, home)).toBe(
      '/data/rdst'
    )
    expect(dataDir('', { RDST_DATA_DIR: '~/other' }, home)).toBe(
      path.join(home, 'other')
    )
    expect(dataDir('', { RDST_DATA_DIR: '~' }, home)).toBe(home)
  })
})
