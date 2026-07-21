import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// vitest isn't configured with globals, so RTL's auto-cleanup never registers —
// clean the DOM between tests so leaked renders don't cause duplicate matches.
afterEach(() => cleanup())

// The detail route imports its report/workload views from the route-ignored
// `-audit-views` sibling (kept out of the /audit route module so the CodeMirror
// stack stays code-split, FIX-1); stub that module so we assert *which* view is
// chosen without pulling the heavy report internals (SQL editor, disclosures)
// into the test.
vi.mock('./-audit-views', () => ({
  AuditReportView: ({ report }: { report: { engine?: string } }) => (
    <div data-testid="report-view">report:{report.engine}</div>
  ),
  WorkloadRunView: ({ run }: { run: { run_id?: string } }) => (
    <div data-testid="workload-view">workload:{run.run_id}</div>
  ),
  formatDate: (iso: string | null | undefined) => `date(${iso})`,
}))

// Keep the real isWorkloadRun discriminator; only the network fetch is stubbed.
const fetchRunDetail = vi.fn()
vi.mock('../lib/useAudit', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/useAudit')>()
  return { ...actual, fetchRunDetail: (id: string) => fetchRunDetail(id) }
})

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => options,
  // autoCodeSplitting rewrites the route's `component` to a lazyRouteComponent
  // call; the tests use the exported page directly, so this just needs to exist.
  lazyRouteComponent: (loader: unknown) => loader,
  Link: ({ to, children }: { to: string; children: ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}))

import type { AuditReport, WorkloadRun } from '../types/audit'
import { AuditRunDetailPage } from './-audit-run-detail-page'
import { Route } from './audit_.runs.$runId'

const routeLoader = (
  Route as unknown as {
    loader: (a: {
      params: { runId: string }
    }) => Promise<AuditReport | WorkloadRun>
  }
).loader

describe('audit run detail loader', () => {
  beforeEach(() => fetchRunDetail.mockReset())

  it('fetches the saved run by the runId path param', async () => {
    const payload = { engine: 'postgres' } as AuditReport
    fetchRunDetail.mockResolvedValue(payload)

    const result = await routeLoader({ params: { runId: 'audit_demo_1' } })

    expect(fetchRunDetail).toHaveBeenCalledWith('audit_demo_1')
    expect(result).toBe(payload)
  })
})

describe('AuditRunDetailPage', () => {
  it('renders a back link that returns to /audit', () => {
    render(
      <AuditRunDetailPage
        data={{ engine: 'postgres' } as AuditReport}
        runId="r1"
      />
    )
    const back = screen.getByRole('link', { name: /back to health check/i })
    expect(back.getAttribute('href')).toBe('/audit')
  })

  it('shows the report view for a metrics audit', () => {
    render(
      <AuditRunDetailPage
        data={{ engine: 'postgres' } as AuditReport}
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
    render(<AuditRunDetailPage data={run} runId="cap_1" />)
    expect(screen.getByTestId('workload-view')).toBeTruthy()
    expect(screen.queryByTestId('report-view')).toBeNull()
  })
})
