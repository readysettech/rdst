import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// vitest isn't configured with globals, so RTL's auto-cleanup never registers —
// clean the DOM between tests so leaked renders don't cause duplicate matches.
afterEach(() => cleanup())

// Stub the canonical report renderers so this test focuses on route selection.
vi.mock('../components/audit/report/AuditReportView', () => ({
  FullAuditReportView: ({ report }: { report: { engine?: string } }) => (
    <div data-testid="report-view">report:{report.engine}</div>
  ),
  WorkloadRunView: ({ run }: { run: { run_id?: string } }) => (
    <div data-testid="workload-view">workload:{run.run_id}</div>
  ),
}))
vi.mock('../components/audit/report/FleetSnapshotView', () => ({
  FleetSnapshotView: ({ detail }: { detail: { snapshot_id: string } }) => (
    <div data-testid="fleet-view">fleet:{detail.snapshot_id}</div>
  ),
}))
vi.mock('../lib/auditReportFormat', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/auditReportFormat')>()),
  formatDate: (iso: string | null | undefined) => `date(${iso})`,
}))

// Keep the real isWorkloadRun discriminator; only the network fetch is stubbed.
const fetchRunDetail = vi.fn()
const fetchFleetSnapshotDetail = vi.fn()
vi.mock('../lib/useAudit', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/useAudit')>()
  return { ...actual, fetchRunDetail: (id: string) => fetchRunDetail(id) }
})
vi.mock('../lib/useFleet', () => ({
  fetchFleetSnapshotDetail: (id: string) => fetchFleetSnapshotDetail(id),
}))

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock()
})

import type { AuditReport, WorkloadRun } from '../types/audit'
import { AuditRunDetailPage } from './-audit-run-detail-page'
import { Route } from './audit_.runs.$runId'

const routeLoader = (
  Route as unknown as {
    loader: (a: {
      params: { runId: string }
    }) => Promise<{ kind: 'audit' | 'fleet'; data: AuditReport | WorkloadRun }>
  }
).loader

describe('audit run detail loader', () => {
  beforeEach(() => {
    fetchRunDetail.mockReset()
    fetchFleetSnapshotDetail.mockReset()
  })

  it('fetches the saved run by the runId path param', async () => {
    const payload = { engine: 'postgres' } as AuditReport
    fetchRunDetail.mockResolvedValue(payload)

    const result = await routeLoader({ params: { runId: 'audit_demo_1' } })

    expect(fetchRunDetail).toHaveBeenCalledWith('audit_demo_1')
    expect(result).toEqual({ kind: 'audit', data: payload })
  })

  it('falls back to a fleet snapshot with the same route contract', async () => {
    const auditError = new Error('not an audit run')
    const snapshot = {
      snapshot_id: 'fleet_1',
      name: 'Production',
      created_at: '2026-07-20',
      targets_audited: 2,
    }
    fetchRunDetail.mockRejectedValue(auditError)
    fetchFleetSnapshotDetail.mockResolvedValue(snapshot)

    await expect(
      routeLoader({ params: { runId: 'fleet_1' } })
    ).resolves.toEqual({ kind: 'fleet', data: snapshot })
    expect(fetchFleetSnapshotDetail).toHaveBeenCalledWith('fleet_1')
  })
})

describe('AuditRunDetailPage', () => {
  it('renders a back link that returns to /audit', () => {
    render(
      <AuditRunDetailPage
        data={{ kind: 'audit', data: { engine: 'postgres' } as AuditReport }}
        runId="r1"
      />
    )
    const back = screen.getByRole('link', { name: /back to reports/i })
    expect(back.getAttribute('href')).toBe('/audit')
  })

  it('shows the report view for a metrics audit', () => {
    render(
      <AuditRunDetailPage
        data={{ kind: 'audit', data: { engine: 'postgres' } as AuditReport }}
        runId="r1"
      />
    )
    expect(screen.getByTestId('report-view')).toBeTruthy()
    expect(screen.queryByTestId('workload-view')).toBeNull()
    expect(screen.getByText('r1')).toBeTruthy()
  })

  it('shows the workload view for a capture run', () => {
    const run = {
      run_id: 'cap_1',
      queries: [],
      started_at: null,
    } as unknown as WorkloadRun
    render(
      <AuditRunDetailPage data={{ kind: 'audit', data: run }} runId="cap_1" />
    )
    expect(screen.getByTestId('workload-view')).toBeTruthy()
    expect(screen.queryByTestId('report-view')).toBeNull()
  })
})
