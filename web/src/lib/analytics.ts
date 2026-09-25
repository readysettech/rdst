import posthog from 'posthog-js'
import {
  type AppErrorEvent,
  installAppErrorCapture,
  scrubEventProperties,
} from './appErrors'
import { isDesktopRuntime } from './desktop'

let analyticsEnabled = false

/**
 * PostHog product analytics + session replay for the rdst UI. One integration
 * covers both surfaces: the browser UI served by the rdst binary and the
 * desktop app (Electron loads the same renderer; `window.rdstDesktop`
 * distinguishes them as a super property on every event).
 *
 * The project key is supplied at build time through `VITE_POSTHOG_KEY`, which
 * only Readyset's release build sets. A build from source, a local `vite
 * build` or one made from the public mirror, carries no key and reports
 * nothing, so a fork is silent without having to opt out. Dev servers never report.
 *
 * Session replay: recording starts only if "Record user sessions" is also
 * enabled in the PostHog project settings. All input fields are masked;
 * the UI runs against customers' databases, so typed values (credentials,
 * SQL literals) must never reach the recording.
 */
export async function initAnalytics(serverEnabled = true): Promise<void> {
  if (!serverEnabled || import.meta.env.DEV) return
  // Automated browsers (Playwright suites in CI and locally) run the
  // production bundle; without this guard every e2e run ships synthetic
  // pageviews and autocapture into the production project as fake users.
  if (navigator.webdriver) return
  // Desktop smoke tests launch the real packaged app without a webdriver;
  // they disable telemetry via RDST_TELEMETRY, which the Electron preload
  // surfaces here. Renderer-side analytics must honor it too.
  if (window.rdstDesktop?.telemetryDisabled) return
  const key = import.meta.env.VITE_POSTHOG_KEY
  if (!key) return
  const person = await fetchTelemetryIdentity()
  if (!person || !analyticsEligible(person)) return

  posthog.init(key, {
    bootstrap: {
      distinctID: person.distinct_id,
      isIdentifiedID: person.account_kind === 'signed_in',
    },
    api_host: import.meta.env.VITE_POSTHOG_HOST || 'https://us.i.posthog.com',
    before_send: (event) =>
      event
        ? { ...event, properties: scrubEventProperties(event.properties) }
        : null,
    loaded: (client) => client.register(analyticsProperties(person)),
    // SPA-aware pageviews: capture on history navigation, not just boot.
    capture_pageview: 'history_change',
    autocapture: true,
    // Automatic exceptions pass through the same property scrubber as named events.
    capture_exceptions: true,
    person_profiles: 'always',
    session_recording: {
      maskAllInputs: true,
    },
  })

  analyticsEnabled = true
  applyAnalyticsIdentity(person)
  installAppErrorCapture((event) => trackEvent('app_error', event))
}

/** Refresh the local person identity after account state changes. */
export function setAnalyticsAccountId(_accountId?: string | null): void {
  if (!analyticsEnabled) return
  void refreshAnalyticsIdentity()
}

interface TelemetryIdentity {
  telemetry_enabled: boolean
  distinct_id: string
  device_id: string
  account_kind: 'signed_in' | 'device_only'
  email: string
  internal_user: boolean
}

let identityRequest = 0
let identifiedId: string | null = null

async function fetchTelemetryIdentity(): Promise<TelemetryIdentity | null> {
  try {
    const response = await fetch('/api/telemetry/identity')
    return response.ok ? ((await response.json()) as TelemetryIdentity) : null
  } catch {
    return null
  }
}

function analyticsEligible(person: TelemetryIdentity): boolean {
  return (
    person.telemetry_enabled &&
    !person.email.trim().toLowerCase().endsWith('@readyset.io')
  )
}

function applyAnalyticsIdentity(person: TelemetryIdentity): void {
  if (identifiedId && identifiedId !== person.distinct_id) posthog.reset()
  posthog.register(analyticsProperties(person))
  if (person.account_kind === 'signed_in') {
    posthog.identify(person.distinct_id, {
      account_kind: person.account_kind,
      ...(person.email ? { email: person.email } : {}),
    })
  } else {
    posthog.register({ distinct_id: person.device_id })
    posthog.setPersonProperties({}, { account_kind: 'device_only' })
  }
  identifiedId = person.distinct_id
}

function analyticsProperties(
  person: TelemetryIdentity
): Record<string, unknown> {
  const desktop = isDesktopRuntime()
  return {
    app: 'rdst',
    platform: desktop ? 'desktop' : 'web',
    surface: desktop ? 'desktop' : 'web',
    is_desktop: desktop,
    ...(window.rdstDesktop?.platform
      ? { os: window.rdstDesktop.platform }
      : {}),
    device_id: person.device_id,
    $device_id: person.device_id,
    account_kind: person.account_kind,
    internal_user: person.internal_user,
  }
}

async function refreshAnalyticsIdentity(): Promise<void> {
  const request = ++identityRequest
  const person = await fetchTelemetryIdentity()
  if (request !== identityRequest || !person) return
  if (!analyticsEligible(person)) {
    analyticsEnabled = false
    posthog.opt_out_capturing()
    posthog.stopSessionRecording()
    return
  }
  applyAnalyticsIdentity(person)
}

/**
 * The surface a user arrived from when they land on a query analysis, and the
 * surface a "Back to ___" affordance returns them to. Shared by
 * `analysis_started` and `back_to_origin` so the two events read as one
 * funnel.
 */
export type AnalyticsOrigin =
  | 'home'
  | 'ask'
  | 'slow-queries'
  | 'scan'
  | 'query-library'

export type SetupGuideStep = 'connect-database' | 'turn-on-monitoring'

export type AnalysisAgeBucket = '<1h' | '1h-24h' | '1d-7d' | '7d+'

/**
 * Why a user chose to re-run rather than keep reading the stored analysis.
 * `stale` is age; `query-changed`/`target-changed` are the sharper causes once
 * the viewer can compare stored context against the registry; `missing-body`
 * is an analysis stored before the full result body was kept; `manual` is a
 * deliberate re-run of a perfectly good analysis.
 */
export type AnalysisRerunReason =
  | 'stale'
  | 'query-changed'
  | 'target-changed'
  | 'missing-body'
  | 'manual'

// Named PostHog events (RDST UX plan, E1). Keep this schema in sync with the
// plan's event table — properties are limited to enums, hashes and buckets;
// SQL text, identifiers and connection details never belong here (session
// replay input masking does not cover event properties).
type RdstEventProperties = {
  setup_step_completed: { step: SetupGuideStep }
  setup_guide_opened: undefined
  setup_guide_dismissed: undefined
  analysis_started: { origin: AnalyticsOrigin }
  analysis_viewed_stored: { age_bucket: AnalysisAgeBucket }
  analysis_rerun: { reason: AnalysisRerunReason }
  compare_run: { query_count: number }
  /**
   * A comparison batch left unfinished: the user navigated away from Compare
   * while at least one query had yet to reach a terminal outcome. `completed`
   * is how many of `query_count` had finished by then.
   */
  compare_abandoned: { query_count: number; completed: number }
  load_test_run: undefined
  nav_item_clicked: { label: string }
  back_to_origin: { origin: AnalyticsOrigin }
  /**
   * An inbox item opened into the page it points at. `kind` is the item type
   * (`query_slower`, `monitoring_issue`, ...) and `band` its severity band.
   */
  inbox_item_opened: {
    kind: string
    band: string
    state: string
    age_bucket: AnalysisAgeBucket
    via: 'database' | 'route'
  }
  /** An uncaught UI error, unhandled rejection or error-boundary catch (lib/appErrors). */
  app_error: AppErrorEvent
}

export type RdstEvent = keyof RdstEventProperties

type EventArgs<E extends RdstEvent> = RdstEventProperties[E] extends undefined
  ? []
  : [properties: RdstEventProperties[E]]

/** Emit a named RDST product event. A no-op until `initAnalytics` succeeds. */
export function trackEvent<E extends RdstEvent>(
  event: E,
  ...args: EventArgs<E>
): void {
  if (!analyticsEnabled) return
  posthog.capture(event, args[0])
}
