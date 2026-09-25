import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import {
  TargetMonitorStatus,
  targetMonitorLine,
} from '@/components/TargetMonitorStatus'
import { settingsFixture } from '@/lib/monitor/fixtures'
import { MonitoringStatusBadge } from './AiMonitoringStatus'
import { monitoringStatus } from './aiMonitoring'
import { PulseHalo } from './PulseHalo'
import { type MonitorPulseState, readingClock } from './useMonitorPulse'

afterEach(cleanup)

const beat = (root: HTMLElement) => root.querySelector('[data-heartbeat="on"]')

describe('the monitoring dot heartbeat', () => {
  it('beats in the Database header while RDST is collecting', () => {
    const status = monitoringStatus({ enabled: true, ai: 'on' })
    const { container, rerender } = render(
      <MonitoringStatusBadge status={status} live />
    )
    expect(beat(container)?.className).toContain('rdst-heartbeat')
    rerender(<MonitoringStatusBadge status={status} />)
    expect(beat(container)).toBeNull()
  })

  it('stays still when monitoring is off or paused', () => {
    for (const status of [
      monitoringStatus({ enabled: false, ai: 'off' }),
      monitoringStatus({ enabled: true, ai: 'on', connectionLost: true }),
    ]) {
      const { container } = render(
        <MonitoringStatusBadge status={status} live />
      )
      expect(beat(container)).toBeNull()
      cleanup()
    }
  })

  it('beats in the target switcher for a live database only', () => {
    const settings = {
      ...settingsFixture('localdev'),
      ai_monitoring: 'on' as const,
    }
    render(<TargetMonitorStatus line={targetMonitorLine(settings, 'live')} />)
    expect(beat(screen.getByTestId('target-monitor-status'))).not.toBeNull()
    cleanup()
    render(<TargetMonitorStatus line={targetMonitorLine(settings, 'late')} />)
    expect(beat(screen.getByTestId('target-monitor-status'))).toBeNull()
  })
})

it('uses a white glow during startup with motion enabled only by preference', () => {
  const pulse = {
    state: 'live',
    beat: 1,
    now: 110,
    last: null,
    clock: readingClock(100, 160, 110),
  } as MonitorPulseState
  const { rerender } = render(<PulseHalo pulse={pulse} startup />)
  const glow = screen.getByTestId('startup-glow')
  expect(glow.className).toContain('bg-white/10')
  expect(glow.className).toContain('motion-safe:animate-pulse')
  expect(screen.getByTestId('reading-ring').classList.contains('hidden')).toBe(
    true
  )
  rerender(<PulseHalo pulse={pulse} startup={false} />)
  expect(screen.queryByTestId('startup-glow')).toBeNull()
  expect(screen.getByTestId('reading-ring').classList.contains('hidden')).toBe(
    false
  )
})
