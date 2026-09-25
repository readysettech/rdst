// Which /api/monitor endpoints the UI reads live. A 'stub' endpoint resolves to
// fixtures from ./fixtures (after the fixture delay) and its stream helper replays
// fixture events. Agent E flips each entry to 'live' when the backend serves the
// brief section 5 shape. See docs/plans/continuous_analysis/CONTRACT.md.
//
// Writes stay 'stub' while the reads they act on are fixtures, so ids and keys
// always come from the same side: enqueueAnalysis follows queries, inboxAction
// follows inbox, reportStart follows reportEvents.

export type Endpoint =
  | 'health'
  | 'pulse'
  | 'collectNow'
  | 'stream'
  | 'queries'
  | 'query'
  | 'queryAnalysis'
  | 'load'
  | 'loadLatency'
  | 'compareRuns'
  | 'series'
  | 'analyzeJobs'
  | 'queryAnalyzeJobs'
  | 'enqueueAnalysis'
  | 'libraryEntry'
  | 'addQuery'
  | 'settings'
  | 'usage'
  | 'muteRules'
  | 'inbox'
  | 'inboxAction'
  | 'inboxStream'
  | 'reportStart'
  | 'reportEvents'
  | 'reportCancel'
  | 'reportHistory'
  | 'report'
  | 'limit'
  | 'aiMonitoring'
  | 'work'

export type EndpointStatus = 'stub' | 'live'

export const CONTRACT_STATUS: Record<Endpoint, EndpointStatus> = {
  health: 'live',
  pulse: 'live',
  collectNow: 'live',
  stream: 'live',
  queries: 'live',
  query: 'live',
  queryAnalysis: 'live',
  load: 'live',
  loadLatency: 'live',
  compareRuns: 'live',
  series: 'live',
  analyzeJobs: 'live',
  queryAnalyzeJobs: 'live',
  enqueueAnalysis: 'live',
  libraryEntry: 'live',
  addQuery: 'live',
  settings: 'live',
  usage: 'live',
  muteRules: 'live',
  inbox: 'live',
  inboxAction: 'live',
  inboxStream: 'live',
  reportStart: 'live',
  reportEvents: 'live',
  reportCancel: 'live',
  reportHistory: 'live',
  report: 'live',
  limit: 'live',
  aiMonitoring: 'live',
  work: 'live',
}

export const FIXTURE_DELAY_MS = 300

// Force fixtures for every endpoint (screenshots, demos, offline work):
// `?monitorFixture=<scenario>` in the URL or localStorage `rdst_monitor_fixture`.
// Scenario names are FixtureScenario in ./fixtures.
const FIXTURE_PARAM = 'monitorFixture'
const FIXTURE_STORAGE_KEY = 'rdst_monitor_fixture'

let override: string | null | undefined

export function fixtureOverride(): string | null {
  if (override !== undefined) return override
  try {
    const fromUrl = new URLSearchParams(window.location.search).get(
      FIXTURE_PARAM
    )
    return fromUrl || window.localStorage.getItem(FIXTURE_STORAGE_KEY) || null
  } catch {
    return null
  }
}

/** Test and story hook: pin the override (string), clear it (null), or reset to URL/storage. */
export function setFixtureOverride(value: string | null | undefined) {
  override = value
}

export function usesFixtures(endpoint: Endpoint): boolean {
  return CONTRACT_STATUS[endpoint] === 'stub' || fixtureOverride() !== null
}
