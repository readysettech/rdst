import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setDesktopBackendState } from '../lib/backendReachability'
import { BackendDownBanner } from './BackendDownBanner'

afterEach(() => {
  cleanup()
  act(() => setDesktopBackendState('running'))
  window.rdstDesktop = undefined
})

describe('BackendDownBanner', () => {
  it('stays hidden while the backend runs', () => {
    render(<BackendDownBanner />)
    expect(screen.queryByTestId('backend-down-banner')).toBeNull()
  })

  it('says the desktop app is restarting RDST, and offers the logs once it keeps failing', () => {
    const openLogs = vi.fn()
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'linux',
      startup: { openLogs },
    }
    render(<BackendDownBanner />)
    act(() => setDesktopBackendState('restarting'))
    expect(screen.getByText('RDST stopped. Restarting...')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Open logs' })).toBeNull()

    act(() => setDesktopBackendState('failed'))
    fireEvent.click(screen.getByRole('button', { name: 'Open logs' }))
    expect(openLogs).toHaveBeenCalled()

    act(() => setDesktopBackendState('running'))
    expect(screen.queryByTestId('backend-down-banner')).toBeNull()
  })

  it('does not promise a restart in a browser', () => {
    render(<BackendDownBanner />)
    act(() => setDesktopBackendState('restarting'))
    expect(screen.getByText('RDST stopped')).toBeTruthy()
  })
})
