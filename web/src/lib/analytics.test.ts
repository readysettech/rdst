import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const captureMock = vi.hoisted(() => vi.fn())
const initMock = vi.hoisted(() => vi.fn())
const registerMock = vi.hoisted(() => vi.fn())

vi.mock('posthog-js', () => ({
  default: {
    init: initMock,
    register: registerMock,
    capture: captureMock,
  },
}))

describe('analytics', () => {
  beforeEach(() => {
    vi.resetModules()
    captureMock.mockClear()
    initMock.mockClear()
    registerMock.mockClear()
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('does not capture events before analytics is initialized', async () => {
    const { trackEvent } = await import('./analytics')
    trackEvent('compare_run', { query_count: 1 })
    expect(captureMock).not.toHaveBeenCalled()
  })

  it('stays a no-op when no PostHog key is configured', async () => {
    const { initAnalytics, trackEvent } = await import('./analytics')
    initAnalytics()
    trackEvent('load_test_run')
    expect(initMock).not.toHaveBeenCalled()
    expect(captureMock).not.toHaveBeenCalled()
  })

  it('captures a named event with its properties once initialized', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics, trackEvent } = await import('./analytics')
    initAnalytics()

    trackEvent('analysis_started', { origin: 'ask' })
    expect(captureMock).toHaveBeenCalledWith('analysis_started', {
      origin: 'ask',
    })

    trackEvent('back_to_origin', { origin: 'scan' })
    expect(captureMock).toHaveBeenCalledWith('back_to_origin', {
      origin: 'scan',
    })

    trackEvent('nav_item_clicked', { label: 'Ask' })
    expect(captureMock).toHaveBeenCalledWith('nav_item_clicked', {
      label: 'Ask',
    })

    trackEvent('compare_run', { query_count: 4 })
    expect(captureMock).toHaveBeenCalledWith('compare_run', {
      query_count: 4,
    })

    trackEvent('compare_abandoned', { query_count: 4, completed: 1 })
    expect(captureMock).toHaveBeenCalledWith('compare_abandoned', {
      query_count: 4,
      completed: 1,
    })
  })

  it('captures property-less events with no second argument', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics, trackEvent } = await import('./analytics')
    initAnalytics()

    trackEvent('load_test_run')
    expect(captureMock).toHaveBeenCalledWith('load_test_run', undefined)
  })

  it('registers rdst as a super property alongside platform on init', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics } = await import('./analytics')
    initAnalytics()

    expect(initMock).toHaveBeenCalledTimes(1)
    expect(registerMock).toHaveBeenCalledWith(
      expect.objectContaining({ app: 'rdst', platform: 'web' })
    )
  })
})
