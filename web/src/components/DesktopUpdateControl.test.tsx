import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DesktopUpdateState } from '../lib/desktop'
import { DesktopUpdateControl } from './DesktopUpdateControl'

describe('DesktopUpdateControl', () => {
  let state: DesktopUpdateState
  const install = vi.fn()

  beforeEach(() => {
    vi.useFakeTimers()
    install.mockClear()
    state = {
      status: 'downloading',
      version: '1.0.9',
      downloadLinks: [],
      progress: 47,
    }
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('shows progress details on hover', () => {
    render(<DesktopUpdateControl state={state} install={install} />)
    fireEvent.mouseEnter(
      screen.getByRole('button', {
        name: '47%: Downloading version 1.0.9',
      })
    )
    act(() => vi.advanceTimersByTime(120))

    expect(screen.getByText('47% downloaded').textContent).toBe(
      '47% downloaded'
    )
  })

  it('explains that an available in-place update starts automatically', () => {
    state = {
      status: 'available',
      version: '1.0.9',
      downloadLinks: [],
    }
    render(<DesktopUpdateControl state={state} install={install} />)
    fireEvent.mouseEnter(
      screen.getByRole('button', {
        name: 'Update: Version 1.0.9 is available',
      })
    )
    act(() => vi.advanceTimersByTime(120))

    expect(
      screen.getByText('The update will start downloading automatically.')
        .textContent
    ).toBe('The update will start downloading automatically.')
  })

  it('turns into a restart action when the update is ready', () => {
    state = {
      status: 'ready',
      version: '1.0.9',
      downloadLinks: [],
      progress: 100,
    }
    render(<DesktopUpdateControl state={state} install={install} />)

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Restart: Version 1.0.9 is ready',
      })
    )
    expect(install).toHaveBeenCalledTimes(1)
  })
})
