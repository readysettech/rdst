import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'

const mutate = vi.hoisted(() => vi.fn())
vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useUpdateMonitorSettings: () => ({ mutate }),
}))

const { TurnOffMonitoringDialog } = await import('./MonitoringSwitch')

afterEach(cleanup)

it('asks in two sentences with a red confirm and a ghost cancel', () => {
  const onClose = vi.fn()
  renderWithClient(
    <TurnOffMonitoringDialog target="demo" open onClose={onClose} />
  )
  const dialog = screen.getByRole('dialog', {
    name: 'Turn off monitoring for demo?',
  })
  expect(dialog.textContent).toContain(
    'RDST stops reading this database and frees one of your AI monitoring spots. Charts keep the history recorded so far, and new changes go unnoticed until you turn it back on.'
  )
  const confirm = screen.getByRole('button', { name: 'Turn off monitoring' })
  expect(confirm.className).toContain('bg-surface-negative-solid')
  expect(confirm.querySelector('svg')).toBeNull()
  fireEvent.click(confirm)
  expect(onClose).toHaveBeenCalledTimes(1)
  expect(mutate).toHaveBeenCalledWith({ enabled: false }, expect.anything())
})
