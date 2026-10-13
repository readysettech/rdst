import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { jsonResponse, renderWithClient } from '@/test-utils'
import { MonitoringSetupDialog } from './MonitoringSetupDialog'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      jsonResponse({
        target: 'demo',
        engine: 'postgresql',
        provider: 'rds',
        provider_name: 'Amazon RDS',
        ready: true,
        checks: [],
      })
    )
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('holds the checker in the shared dialog with one ghost Done', () => {
  const onClose = vi.fn()
  renderWithClient(
    <MonitoringSetupDialog target="demo" open onClose={onClose} />
  )
  expect(screen.getByTestId('monitoring-setup')).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Monitoring setup' })).toBeTruthy()
  expect(
    screen.getByText(
      'RDST checks what demo lets it read. Fix a step and it checks again on its own.'
    )
  ).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Done' }))
  expect(onClose).toHaveBeenCalledTimes(1)
})
