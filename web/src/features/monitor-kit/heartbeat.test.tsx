import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import {
  TargetMonitorStatus,
  targetMonitorLine,
} from '@/components/TargetMonitorStatus'
import { settingsFixture } from '@/lib/monitor/fixtures'

afterEach(cleanup)

const beat = (root: HTMLElement) => root.querySelector('[data-heartbeat="on"]')

describe('the monitoring dot heartbeat', () => {
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
