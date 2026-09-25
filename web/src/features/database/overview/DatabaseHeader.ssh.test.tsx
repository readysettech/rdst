import { cleanup, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import {
  type MonitorPulseState,
  readingClock,
} from '@/features/monitor-kit/useMonitorPulse'
import { renderWithClient } from '@/test-utils'
import { DatabaseHeader } from './DatabaseHeader'

vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useMonitoringLimit: () => ({ data: undefined }),
}))

afterEach(cleanup)

const reason = 'SSH tunnel host not found: bastion.example.com'

function renderHeader(state: MonitorPulseState['state']) {
  const collect = {
    ts: 1000,
    kind: 'collect' as const,
    ok: false,
    detail: reason,
    next_collect_at: 1060,
    state: 'connection_lost' as const,
  }
  renderWithClient(
    <DatabaseHeader
      target="db"
      health={undefined}
      settings={undefined}
      pulse={{
        pulses: [collect],
        last: collect,
        lastCollect: collect,
        state,
        nextAt: 1060,
        now: 1020,
        clock: readingClock(1000, 1060, 1020),
        beat: 0,
        isLoading: false,
      }}
      glyphTone="negative"
      view="live"
      onViewChange={vi.fn()}
      onOpenSettings={vi.fn()}
      onEnable={vi.fn()}
      onCheckAgain={vi.fn()}
    />
  )
}

it('names the SSH failure in the Database connection state', () => {
  renderHeader('connection_lost')
  expect(screen.getByText(reason)).toBeTruthy()
})

it('hides the previous SSH failure when readings resume', () => {
  renderHeader('live')
  expect(screen.queryByText(reason)).toBeNull()
})
