import { describe, expect, it } from 'vitest'

import distributions from '../../distributions.json' with { type: 'json' }
import { distributionRules } from './distribution.js'

describe('distributionRules', () => {
  it('reads a build without a distribution as the direct download', () => {
    expect(distributionRules(undefined).name).toBe('direct')
    expect(distributionRules('')).toEqual(distributionRules('direct'))
  })

  it('asks before sharing anything or starting at sign-in only in the Store build', () => {
    expect(distributionRules('direct')).toMatchObject({
      consent: 'opt_out',
      startAtLogin: 'on',
    })
    expect(distributionRules('microsoft-store')).toMatchObject({
      consent: 'opt_in',
      startAtLogin: 'ask',
    })
  })

  it('installs the Store build machine-wide, updating from a channel of its own', () => {
    expect(distributionRules('direct')).toMatchObject({
      installScope: 'user',
      windowsUpdates: 'windows',
    })
    expect(distributionRules('microsoft-store')).toMatchObject({
      installScope: 'machine',
      windowsUpdates: 'windows-store',
    })
  })

  it('refuses a distribution the file does not list', () => {
    expect(() => distributionRules('mac-app-store')).toThrow(
      'Unknown RDST Desktop distribution: mac-app-store'
    )
  })

  it('gives every distribution the same rules to set', () => {
    const keys = Object.values(distributions).map((rules) =>
      Object.keys(rules).sort().join(',')
    )
    expect(new Set(keys).size).toBe(1)
  })
})
