import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '../../test-utils'
import { SetupGuideHelpEntry } from './SetupGuideHelpEntry'
import {
  __resetSetupGuideStoreForTests,
  dismissSetupGuide,
  getSetupGuideState,
} from './setupGuideStore'
import type { SetupProgress } from './setupModel'

vi.mock('../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'orders', setTarget: vi.fn() }),
}))

vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

function progress(overrides: Partial<SetupProgress> = {}): SetupProgress {
  return { target: 'orders', connected: true, ...overrides }
}

function stubProgress(value: SetupProgress, monitoring = false) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      url.startsWith('/api/monitor/')
        ? new Response(JSON.stringify({ enabled: monitoring }), { status: 200 })
        : new Response(JSON.stringify(value), { status: 200 })
    )
  )
}

beforeEach(() => __resetSetupGuideStoreForTests())

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SetupGuideHelpEntry', () => {
  it('stays absent while the guide itself is on screen', async () => {
    stubProgress(progress())
    renderWithClient(<SetupGuideHelpEntry className="nav" />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-guide-help-entry')).toBeNull()
  })

  it('appears once the guide is dismissed with steps left', async () => {
    dismissSetupGuide()
    stubProgress(progress())
    renderWithClient(<SetupGuideHelpEntry className="nav" />)

    expect(await screen.findByTestId('setup-guide-help-entry')).toBeTruthy()
  })

  it('stays absent for a finished setup, dismissed or not', async () => {
    dismissSetupGuide()
    stubProgress(progress(), true)
    renderWithClient(<SetupGuideHelpEntry className="nav" />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('setup-guide-help-entry')).toBeNull()
  })

  it('undoes the dismissal so the sidebar block comes back', async () => {
    dismissSetupGuide()
    stubProgress(progress())
    renderWithClient(<SetupGuideHelpEntry className="nav" />)

    const entry = await screen.findByTestId('setup-guide-help-entry')
    expect(entry.textContent).toContain('Show setup guide')
    fireEvent.click(entry)

    expect(getSetupGuideState().dismissed).toBe(false)
    // Its own condition is gone, so the entry retires with the same click.
    expect(screen.queryByTestId('setup-guide-help-entry')).toBeNull()
  })
})
