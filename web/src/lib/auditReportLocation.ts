/**
 * Inner report tab identities and the URL sync they read and write. The report
 * keeps its own tab state in the query string so a section is deep-linkable
 * without a route change.
 */

import { useEffect, useState } from 'react'

export type InnerReportTab =
  | 'overview'
  | 'queries'
  | 'sizing'
  | 'savings'
  | 'detailed-analysis'
  | 'next-steps'

export type QueryReportTab = 'captured' | 'historical'
export const INNER_REPORT_TABS: Array<{
  id: InnerReportTab
  label: string
}> = [
  { id: 'overview', label: 'Overview' },
  { id: 'queries', label: 'Queries' },
  { id: 'sizing', label: 'Sizing' },
  { id: 'savings', label: 'Savings' },
  { id: 'detailed-analysis', label: 'Detailed Analysis' },
  { id: 'next-steps', label: 'Next Steps' },
]

export const INNER_REPORT_TAB_DESCRIPTIONS: Record<InnerReportTab, string> = {
  overview:
    'See the headline verdict and the findings that matter most for this database.',
  queries:
    'Review the queries observed on this database—captured live during the audit window and historically busiest—and whether Readyset can cache them.',
  sizing:
    'Compare how this instance is provisioned with the capacity its observed workload actually needs.',
  savings:
    'See the estimated monthly savings from right-sizing this instance and caching eligible queries with Readyset.',
  'detailed-analysis':
    'Inspect database health across configuration, memory and cache, vacuum and bloat, indexes, connections, and replication.',
  'next-steps':
    'Work through the recommended actions for this database, ordered by expected impact.',
}

export type FleetReportTab = 'summary' | 'sizing'

export const FLEET_REPORT_TABS: Array<{
  id: FleetReportTab
  label: string
}> = [
  { id: 'summary', label: 'Fleet Summary' },
  { id: 'sizing', label: 'Fleet Savings' },
]

export const FLEET_REPORT_TAB_DESCRIPTIONS: Record<FleetReportTab, string> = {
  summary:
    'See the fleet-wide health verdict, audit coverage, and highest-priority findings across all instances.',
  sizing:
    'Review current and suggested monthly fleet costs, total potential savings, and the per-instance rollup.',
}

export const REPORT_LOCATION_EVENT = 'rdst:report-location-change'

export function currentSearchParams(
  _locationVersion?: number
): URLSearchParams {
  return typeof window === 'undefined'
    ? new URLSearchParams()
    : new URLSearchParams(window.location.search)
}

export function updateReportLocation(
  patch: Record<string, string | undefined>,
  { replace = false }: { replace?: boolean } = {}
) {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  for (const [key, value] of Object.entries(patch)) {
    if (value) url.searchParams.set(key, value)
    else url.searchParams.delete(key)
  }
  const next = `${url.pathname}${url.search}${url.hash}`
  if (replace) window.history.replaceState(window.history.state, '', next)
  else window.history.pushState(window.history.state, '', next)
  window.dispatchEvent(new Event(REPORT_LOCATION_EVENT))
}

export function useReportLocationVersion(): number {
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const changed = () => setVersion((value) => value + 1)
    window.addEventListener('popstate', changed)
    window.addEventListener(REPORT_LOCATION_EVENT, changed)
    return () => {
      window.removeEventListener('popstate', changed)
      window.removeEventListener(REPORT_LOCATION_EVENT, changed)
    }
  }, [])
  return version
}

export const QUERY_FOCUS_EVENT = 'rdst:focus-audit-query'
