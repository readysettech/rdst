import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  forgetEnableAfterSignIn,
  rememberEnableAfterSignIn,
  takeEnableAfterSignIn,
} from './pendingMonitorEnable'

afterEach(() => {
  forgetEnableAfterSignIn()
  vi.useRealTimers()
})

describe('turning monitoring on after a sign-in that leaves the page', () => {
  it('hands the choices back once, for the same database', () => {
    rememberEnableAfterSignIn({
      target: 'shop',
      automatic: false,
      measure: true,
    })
    expect(takeEnableAfterSignIn('other')).toBeNull()
    expect(takeEnableAfterSignIn('shop')).toEqual({
      target: 'shop',
      automatic: false,
      measure: true,
    })
    expect(takeEnableAfterSignIn('shop')).toBeNull()
  })

  it('drops a request older than half an hour', () => {
    vi.useFakeTimers()
    rememberEnableAfterSignIn({
      target: 'shop',
      automatic: true,
      measure: true,
    })
    vi.advanceTimersByTime(31 * 60 * 1000)
    expect(takeEnableAfterSignIn('shop')).toBeNull()
  })

  it('ignores a stored value it cannot read', () => {
    localStorage.setItem('rdst_enable_after_signin', '{not json')
    expect(takeEnableAfterSignIn('shop')).toBeNull()
    expect(localStorage.getItem('rdst_enable_after_signin')).toBeNull()
  })
})
