import { afterEach, describe, expect, it } from 'vitest'
import { isDesktopFrameless } from './desktop'

function setDesktopPlatform(platform: string) {
  window.rdstDesktop = { isDesktop: true, platform }
}

afterEach(() => {
  delete window.rdstDesktop
})

describe('isDesktopFrameless', () => {
  it.each(['linux', 'win32'])('enables renderer controls on %s', (platform) => {
    setDesktopPlatform(platform)

    expect(isDesktopFrameless()).toBe(true)
  })

  it('uses native controls on macOS', () => {
    setDesktopPlatform('darwin')

    expect(isDesktopFrameless()).toBe(false)
  })

  it('is false outside the desktop shell', () => {
    expect(isDesktopFrameless()).toBe(false)
  })
})
