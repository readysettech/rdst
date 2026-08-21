import posthog from 'posthog-js'
import { isDesktopRuntime } from './desktop'

// PostHog project ingest keys are public, write-only identifiers. Keep the
// production bundle independent of release-worker IAM while still allowing a
// different project to be selected explicitly at build time. Local builds
// remain silent unless VITE_POSTHOG_KEY is set.
const PRODUCTION_POSTHOG_KEY = 'phc_WPINnbS1CUiADz01QFeDZCr4Wn7jXfNPxe1EK0V2ZzP'

let analyticsEnabled = false

/**
 * PostHog product analytics + session replay for the rdst UI. One integration
 * covers both surfaces: the browser UI served by the rdst binary and the
 * desktop app (Electron loads the same renderer; `window.rdstDesktop`
 * distinguishes them as a super property on every event).
 *
 * The project key is public by design. Production builds use the same
 * write-only ingest key as the CLI unless `VITE_POSTHOG_KEY` overrides it.
 * Local development remains a no-op unless an explicit key is provided.
 *
 * Session replay: recording starts only if "Record user sessions" is also
 * enabled in the PostHog project settings. All input fields are masked —
 * the UI runs against customers' databases, so typed values (credentials,
 * SQL literals) must never reach the recording.
 */
export function initAnalytics(): void {
  // Automated browsers (Playwright suites in CI and locally) run the
  // production bundle; without this guard every e2e run ships synthetic
  // pageviews and autocapture into the production project as fake users.
  if (navigator.webdriver) return
  // Desktop smoke tests launch the real packaged app without a webdriver;
  // they disable telemetry via RDST_TELEMETRY, which the Electron preload
  // surfaces here. Renderer-side analytics must honor it too.
  if (window.rdstDesktop?.telemetryDisabled) return
  const key =
    import.meta.env.VITE_POSTHOG_KEY ||
    (import.meta.env.PROD ? PRODUCTION_POSTHOG_KEY : '')
  if (!key) return

  posthog.init(key, {
    api_host: import.meta.env.VITE_POSTHOG_HOST || 'https://us.i.posthog.com',
    // SPA-aware pageviews: capture on history navigation, not just boot.
    capture_pageview: 'history_change',
    autocapture: true,
    // Error Tracking: uncaught exceptions and unhandled rejections become
    // $exception events. Stack frames only; PostHog masks none of our data
    // because none is attached.
    capture_exceptions: true,
    person_profiles: 'identified_only',
    session_recording: {
      maskAllInputs: true,
    },
  })

  posthog.register({
    app: 'rdst',
    platform: isDesktopRuntime() ? 'desktop' : 'web',
    ...(window.rdstDesktop?.platform
      ? { os: window.rdstDesktop.platform }
      : {}),
  })

  analyticsEnabled = true
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

export type SetupGuideStep =
  | 'connect-database'
  | 'build-schema'
  | 'find-queries'
  | 'analyze-query'
  | 'compare'

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
