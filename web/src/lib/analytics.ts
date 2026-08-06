import posthog from 'posthog-js'
import { isDesktopRuntime } from './desktop'

/**
 * PostHog product analytics + session replay for the rdst UI. One integration
 * covers both surfaces: the browser UI served by the rdst binary and the
 * desktop app (Electron loads the same renderer; `window.rdstDesktop`
 * distinguishes them as a super property on every event).
 *
 * The project key ships in the client bundle via `VITE_POSTHOG_KEY` (public
 * by design, set at build time). Without a key this is a no-op, so local dev
 * and self-builds send nothing.
 *
 * Session replay: recording starts only if "Record user sessions" is also
 * enabled in the PostHog project settings. All input fields are masked —
 * the UI runs against customers' databases, so typed values (credentials,
 * SQL literals) must never reach the recording.
 */
export function initAnalytics(): void {
  const key = import.meta.env.VITE_POSTHOG_KEY
  if (!key) return

  posthog.init(key, {
    api_host: import.meta.env.VITE_POSTHOG_HOST || 'https://us.i.posthog.com',
    // SPA-aware pageviews: capture on history navigation, not just boot.
    capture_pageview: 'history_change',
    autocapture: true,
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
