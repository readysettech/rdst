import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { MonitorSettings } from '@/lib/monitor/types'
import { TargetMonitorStatus, targetMonitorLine } from './TargetMonitorStatus'

const settings = (patch: Partial<MonitorSettings>) =>
  ({ enabled: true, ai_monitoring: 'on', ...patch }) as MonitorSettings

it('names each monitoring state in words, not only color', () => {
  expect(targetMonitorLine(settings({}), 'live')).toMatchObject({
    dot: 'live',
    label: 'AI monitoring on',
    ai: true,
  })
  expect(
    targetMonitorLine(settings({ ai_monitoring: 'local' }), 'live')
  ).toMatchObject({ dot: 'live', label: 'Local monitoring only', ai: false })
  expect(targetMonitorLine(settings({ enabled: false }), 'paused')).toMatchObject(
    { dot: 'hollow', label: 'Monitoring off', monitored: false }
  )
  expect(targetMonitorLine(settings({}), 'connection_lost')).toMatchObject({
    dot: 'still',
    tone: 'negative',
    label: "Can't reach the database",
  })
  expect(targetMonitorLine(settings({}), 'late')).toMatchObject({
    dot: 'still',
    reason: 'Readings delayed',
  })
  expect(targetMonitorLine(undefined, undefined, { failed: true })).toMatchObject(
    { label: "Status didn't load" }
  )
})

it('animates the dot only while collecting', () => {
  render(<TargetMonitorStatus line={targetMonitorLine(settings({}), 'live')} />)
  expect(screen.getByTestId('target-monitor-status').dataset.dot).toBe('live')
  expect(screen.getByText('AI monitoring on')).toBeTruthy()
})
