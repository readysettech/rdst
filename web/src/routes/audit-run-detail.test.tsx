import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
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

const navigateSpy = vi.fn()
vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock({ useNavigate: () => navigateSpy })
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

describe('the route boundaries', () => {
  // autoCodeSplitting rewrites the boundaries it splits into an arrow function
  // importing the route's split chunk; the ones it leaves alone are still the
  // function declarations from this file (so they carry a prototype).
  const boundary = async (name: 'pendingComponent' | 'errorComponent') => {
    const node = (Route as unknown as Record<string, unknown>)[name] as
      | ComponentType<{ error: Error }>
      | (() => Promise<Record<string, ComponentType<{ error: Error }>>>)
    if (Object.hasOwn(node, 'prototype')) {
      return node as ComponentType<{ error: Error }>
    }
    const split = await (
      node as () => Promise<Record<string, ComponentType<{ error: Error }>>>
    )()
    return split[name]
  }

  it('skeletons the report it is about to show', async () => {
    const Boundary = await boundary('pendingComponent')
    render(<Boundary error={new Error('unused')} />)

    // Was a bare spinner over an empty page, so the layout jumped. [E-47]
    expect(screen.getByText('Loading saved run')).toBeTruthy()
    expect(document.querySelectorAll('#skeleton').length).toBeGreaterThan(0)
  })

  it('says a missing run is gone and routes back, not "Failed to fetch: 404"', async () => {
    const Boundary = await boundary('errorComponent')
    render(<Boundary error={new Error('Failed to fetch audit run: 404')} />)

    // The failure surface is the shared one: alert role, human sentence,
    // a routed action, and the raw client string behind the expander. [E-25]
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByText('This run is no longer saved')).toBeTruthy()
    expect(screen.queryByText(/Failed to fetch audit run/)).toBeNull()
    expect(
      screen.getAllByRole('button', { name: /Back to Reports/ }).length
    ).toBeGreaterThan(0)
    expect(
      screen.getByRole('button', { name: 'Technical details' })
    ).toBeTruthy()
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

  it('carries the skipped-step reasons from the saved run onto the report', () => {
    const run = {
      run_id: 'cap_2',
      queries: [],
      started_at: null,
      analysis_error: 'the model provider returned 503',
      readyset_notice: 'Readyset benchmark skipped: no Docker',
    } as unknown as WorkloadRun
    render(
      <AuditRunDetailPage data={{ kind: 'audit', data: run }} runId="cap_2" />
    )

    expect(screen.getByText('Analysis skipped')).toBeTruthy()
    expect(screen.getByText('the model provider returned 503')).toBeTruthy()
    expect(screen.getByText('Readyset benchmark skipped')).toBeTruthy()
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
