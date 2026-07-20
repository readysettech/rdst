import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DesktopUpdateState } from './desktop'
import { useDesktopUpdates } from './useDesktopUpdates'

function Probe() {
  const { state, install } = useDesktopUpdates()
  return (
    <button type="button" onClick={install}>
      {state ? `${state.status}:${state.progress ?? ''}` : 'none'}
    </button>
  )
}

describe('useDesktopUpdates', () => {
  let stateCallback: ((state: DesktopUpdateState) => void) | null = null
  const install = vi.fn()

  beforeEach(() => {
    stateCallback = null
    install.mockClear()
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'linux',
      updates: {
        getState: () => Promise.resolve(null),
        install,
        onStateChange: (callback) => {
          stateCallback = callback
          return () => {
            stateCallback = null
          }
        },
      },
    }
  })

  afterEach(() => {
    cleanup()
    delete window.rdstDesktop
  })

  it('tracks download progress and the ready state', async () => {
    render(<Probe />)
    await act(async () => {})

    act(() =>
      stateCallback?.({
        status: 'downloading',
        version: '1.0.9',
        downloadLinks: [],
        progress: 47,
      })
    )
    expect(screen.getByRole('button').textContent).toBe('downloading:47')

    act(() =>
      stateCallback?.({
        status: 'ready',
        version: '1.0.9',
        downloadLinks: [],
        progress: 100,
      })
    )
    expect(screen.getByRole('button').textContent).toBe('ready:100')
  })

  it('forwards the restart action to the desktop shell', async () => {
    render(<Probe />)
    await act(async () => {})

    fireEvent.click(screen.getByRole('button'))
    expect(install).toHaveBeenCalledTimes(1)
  })

  it('unsubscribes when the consumer unmounts', async () => {
    const rendered = render(<Probe />)
    await act(async () => {})
    expect(stateCallback).not.toBeNull()

    rendered.unmount()
    expect(stateCallback).toBeNull()
  })
})
