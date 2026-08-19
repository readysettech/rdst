import posthog from 'posthog-js'
import { isDesktopRuntime } from './desktop'

// PostHog project ingest keys are public, write-only identifiers. Keep the
// production bundle independent of release-worker IAM while still allowing a
// different project to be selected explicitly at build time. Local builds
// remain silent unless VITE_POSTHOG_KEY is set.
const PRODUCTION_POSTHOG_KEY = 'phc_WPINnbS1CUiADz01QFeDZCr4Wn7jXfNPxe1EK0V2ZzP'

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
}
