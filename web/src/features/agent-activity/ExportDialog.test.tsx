import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { ExportDialog } from './ExportDialog'

afterEach(cleanup)

it('offers one plain Download CSV and a ghost cancel', () => {
  const onClose = vi.fn()
  renderWithClient(
    <ExportDialog
      open
      onClose={onClose}
      targets={['demo']}
      initial={{ range: '7d' }}
    />
  )
  expect(
    screen.getByRole('heading', { name: 'Export agent activity' })
  ).toBeTruthy()
  const download = screen.getByRole('button', { name: 'Download CSV' })
  expect(download.querySelector('svg')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(onClose).toHaveBeenCalledTimes(1)
})

it('says how many days the log keeps, from the page', () => {
  renderWithClient(
    <ExportDialog
      open
      onClose={vi.fn()}
      targets={['demo']}
      retentionDays={90}
      initial={{ range: '7d' }}
    />
  )
  expect(
    screen.getByText(
      'Whole days in your time zone, ending today. The log keeps 90 days.'
    )
  ).toBeTruthy()
})
