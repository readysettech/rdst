import { cleanup, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { renderWithClient } from '@/test-utils'
import type { ContainerProgress } from '../../lib/useDemo'
import { StartingCard } from './StartingCard'

afterEach(cleanup)

function container(
  name: string,
  state: ContainerProgress['state'],
  detail?: string
): ContainerProgress {
  return { name, label: name, state, percent: 0, detail }
}

describe('StartingCard', () => {
  it('shows the six steps with the active download named by component', () => {
    renderWithClient(
      <StartingCard
        containers={[
          container(
            'pg',
            'starting',
            'Pulling public.ecr.aws/i9r8k6s4/readyset:1.2 (2/4, ~300 MB)...'
          ),
          container('readyset', 'pending'),
          container('sqp', 'pending'),
          container('qp-cron', 'pending'),
        ]}
        phase="provisioning"
        loadRunning={false}
        percent={42}
        startedAt={Date.now() - 48_000}
      />
    )
    const card = screen.getByTestId('platform-starting-card')
    expect(
      within(card).getByRole('heading', { level: 2, name: 'Starting the demo' })
    ).toBeTruthy()
    expect(within(card).getByText('step 2 of 6')).toBeTruthy()
    expect(within(card).getByText('48 s')).toBeTruthy()
    const items = within(card).getAllByRole('listitem')
    expect(items).toHaveLength(6)
    expect(items[0].textContent).toContain('Docker is running')
    expect(items[1].getAttribute('aria-current')).toBe('step')
    expect(items[1].textContent).toContain(
      'Downloading the Readyset cache engine'
    )
    expect(items[1].textContent).toContain('2 of 4 images')
    expect(items[1].textContent).toContain('~300 MB')
    expect(card.textContent).not.toContain('public.ecr.aws')
    expect(
      within(items[1]).getByRole('progressbar', { name: 'Download progress' })
    ).toBeTruthy()
    expect(items[5].textContent).toContain('then the comparison opens')
  })

  it('ticks the finished components and moves the active step on', () => {
    renderWithClient(
      <StartingCard
        containers={[
          container('pg', 'ready'),
          container('readyset', 'starting', 'Waiting for Readyset (3s)...'),
          container('sqp', 'pending'),
          container('qp-cron', 'pending'),
        ]}
        phase="provisioning"
        loadRunning={false}
        percent={60}
        startedAt={Date.now()}
      />
    )
    expect(screen.getByText('step 4 of 6')).toBeTruthy()
    const items = screen.getAllByRole('listitem')
    expect(items[3].getAttribute('aria-current')).toBe('step')
    expect(items[3].textContent).toContain('Waiting for Readyset (3s)...')
    expect(items[2].textContent).toContain('0 s')
    expect(screen.queryByRole('progressbar')).toBeNull()
  })
})
