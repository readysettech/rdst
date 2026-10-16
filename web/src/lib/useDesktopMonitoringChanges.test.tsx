import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { useDesktopMonitoringChanges } from './useDesktopMonitoringChanges'

afterEach(() => {
  delete window.rdstDesktop
})

it('refetches monitoring when the tray changes it, until unmounted', () => {
  let notify = () => {}
  const unsubscribe = vi.fn()
  window.rdstDesktop = {
    isDesktop: true,
    platform: 'linux',
    monitoring: {
      onChange: (callback) => {
        notify = callback
        return unsubscribe
      },
    },
  }
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )

  const { unmount } = renderHook(() => useDesktopMonitoringChanges(), {
    wrapper,
  })
  notify()

  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['monitor'] })
  unmount()
  expect(unsubscribe).toHaveBeenCalledTimes(1)
})

it('does nothing outside the desktop app', () => {
  const client = new QueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  expect(() =>
    renderHook(() => useDesktopMonitoringChanges(), { wrapper }).unmount()
  ).not.toThrow()
})
