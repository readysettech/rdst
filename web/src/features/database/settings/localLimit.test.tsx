import { cleanup, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'

const message =
  'This install already monitors 5 databases. The local limit is 5. Turn off monitoring for a database before turning on another.'

vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useMonitoringLimit: () => ({ data: undefined, isLoading: false }),
  useUpdateMonitorSettings: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: true,
    error: new Error(message),
  }),
}))
vi.mock('@/features/prerequisites', async (original) => ({
  ...(await original<typeof import('@/features/prerequisites')>()),
  usePrerequisites: () => ({ phase: 'ready', recheck: vi.fn() }),
  PrerequisitesChecker: () => null,
}))

const { EnableMonitoringDialog } = await import('./EnableMonitoringDialog')

afterEach(cleanup)

it('shows a local limit refusal next to the turn-on action', () => {
  const onClose = vi.fn()
  renderWithClient(
    <EnableMonitoringDialog target="sixth" open onClose={onClose} />
  )
  expect(screen.getAllByText(message).length).toBeGreaterThan(0)
  expect(
    screen.getByRole('button', { name: 'Turn on monitoring' })
  ).toBeTruthy()
  expect(onClose).not.toHaveBeenCalled()
})
