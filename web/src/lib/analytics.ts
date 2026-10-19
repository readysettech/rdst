import posthog, {
  type CapturedNetworkRequest,
  type SessionRecordingOptions,
} from 'posthog-js'
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
  const buildKey = import.meta.env.VITE_POSTHOG_KEY
  if (!buildKey) return
  const person = await fetchTelemetryIdentity()
  const key = person && reportingKey(buildKey, person)
  if (!person || !key) return
  activeKey = key

  posthog.init(key, {
    bootstrap: {
      distinctID: person.distinct_id,
      isIdentifiedID: person.account_kind === 'signed_in',
    },
    api_host: import.meta.env.VITE_POSTHOG_HOST || 'https://us.i.posthog.com',
    // Replay snapshots carry the rrweb DOM stream, which the property scrubber would
    // truncate into an unplayable recording; the session_recording masking below
    // governs what replay captures.
    before_send: (event) => {
      if (!event) return null
      if (event.event === '$snapshot') return event
      return { ...event, properties: scrubEventProperties(event.properties) }
    },
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

  // An opt-out from an earlier ineligible session persists in storage; this person reports.
  if (posthog.has_opted_out_capturing()) {
    posthog.opt_in_capturing()
    posthog.startSessionRecording()
  }
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
  // Where the local server reports this person: dev builds, test runs and Readyset's own
  // people never use production. A preview project comes with its key.
  posthog_project?: 'production' | 'preview' | 'none'
  posthog_key?: string | null
}

// The production project's write-only ingest key (project 260434).
export const PRODUCTION_POSTHOG_KEY =
  'phc_WPINnbS1CUiADz01QFeDZCr4Wn7jXfNPxe1EK0V2ZzP'

let activeKey: string | null = null

/**
 * The key this person's events use, or null for none: the server's preview key when it
 * routes them there, and production only when the server reports there too. Readyset
 * accounts never count as production users.
 */
function reportingKey(
  buildKey: string,
  person: TelemetryIdentity
): string | null {
  if (!person.telemetry_enabled) return null
  if (person.posthog_project === 'preview' && person.posthog_key)
    return person.posthog_key
  if (
    buildKey === PRODUCTION_POSTHOG_KEY &&
    person.posthog_project !== 'production'
  )
    return null
  if (person.email?.trim().toLowerCase().endsWith('@readyset.io')) return null
  return buildKey
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
  // A key can't change mid-session: a person now routed elsewhere stops reporting.
  const key = reportingKey(import.meta.env.VITE_POSTHOG_KEY ?? '', person)
  if (key !== activeKey) {
    analyticsEnabled = false
    posthog.opt_out_capturing()
    posthog.stopSessionRecording()
    return
  }
  if (!analyticsEnabled && activeKey) {
    // Eligible again, e.g. after signing out of an internal account.
    posthog.opt_in_capturing()
    posthog.startSessionRecording()
    analyticsEnabled = true
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

/** How a database reaches RDST; sent as `X-RDST-Via` on add and test requests. */
export type DatabaseAddVia = 'url' | 'fields' | 'provider' | 'agent'

/** Get started steps, and the first-run tour's scenes (`tour-*`). */
export type OnboardingStep =
  | 'add-database'
  | 'monitor'
  | 'sign-in'
  | 'tour-connect'
  | 'tour-watch'
  | 'tour-inbox'
  | 'tour-agent'

export type SigninPromptState = 'soft' | 'budget' | 'required'

/** The view the add-database dialog opened on. */
export type AddDatabaseEntry = 'form' | 'agent' | 'provider'

/** A connection form field, by name only. */
export type DatabaseAddField =
  | 'connection_string'
  | 'host'
  | 'port'
  | 'database'
  | 'user'
  | 'name'

export type DatabaseAddFieldError = 'missing' | 'invalid' | 'unreadable'

/**
 * Where an add-database attempt stood when it stopped, on any path: the connection form's
 * stages, the agent path's before the database lands, then the turn-on dialog's.
 */
export type DatabaseAddStage =
  | 'untouched'
  | 'filling'
  | 'field_error'
  | 'testing'
  | 'test_failed'
  | 'tested_ok'
  | 'saving'
  | 'prompt_loading'
  | 'prompt_error'
  | 'prompt_shown'
  | 'paired'
  | 'database_added'
  | 'agent_error'
  | 'monitoring_checking'
  | 'monitoring_unreachable'
  | 'monitoring_blocked'
  | 'monitoring_ready'

/** A fixed set, so an error's text (hosts, users, database names) never reaches analytics. */
export type UserErrorClass =
  | 'auth_failed'
  | 'host_unreachable'
  | 'timeout'
  | 'tls'
  | 'db_not_found'
  | 'permission_denied'
  | 'ssh_failed'
  | 'unknown'

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
  /** The first screen of an app session rendered. */
  first_screen_rendered: { gated: boolean }
  database_add_opened: { via_entry: string; entry: AddDatabaseEntry }
  database_add_path_selected: { via: DatabaseAddVia }
  database_add_test_started: { engine: string; via: DatabaseAddVia }
  /** A submit found these fields unusable; once per field per submit. Form only. */
  database_add_field_error: {
    field: DatabaseAddField
    error_class: DatabaseAddFieldError
  }
  /** A connection test on the way in finished; once per outcome per attempt. */
  database_add_test_result: {
    ok: boolean
    error_class: UserErrorClass | null
    via: DatabaseAddVia
  }
  /** An attempt stopped before monitoring was on; once per attempt. */
  database_add_abandoned: {
    last_stage: DatabaseAddStage
    via: DatabaseAddVia
    seconds_open: number
  }
  /** `attempt_id` joins the server's `agent_connect_step` events; never the code itself. */
  agent_prompt_shown: { attempt_id: string | null }
  /** `via` is how the database arrived, or `page` when opened from its page. */
  monitoring_dialog_shown: { via: DatabaseAddVia | 'page' }
  monitoring_prereq_failed: { check: string }
  signin_prompt_shown: { state: SigninPromptState }
  signin_started: { method: 'google' | 'email' }
  signin_completed: undefined
  signin_abandoned: { stage: 'choose' | 'link_sent' | 'callback' }
  onboarding_step_viewed: { step: OnboardingStep }
  onboarding_step_completed: { step: OnboardingStep }
  inbox_first_viewed: undefined
  user_error_shown: { surface: string; error_class: UserErrorClass }
}

export type RdstEvent = keyof RdstEventProperties

type EventArgs<E extends RdstEvent> = RdstEventProperties[E] extends undefined
  ? []
  : [properties: RdstEventProperties[E]]

// Events from the first render land before `initAnalytics` settles; they wait here
// and go out once it succeeds, or are dropped once it decides not to report.
const MAX_PENDING_EVENTS = 50
let analyticsSettled = false
let pendingEvents: [RdstEvent, unknown][] = []

/** Emit a named RDST product event. A no-op once `initAnalytics` declines to report. */
export function trackEvent<E extends RdstEvent>(
  event: E,
  ...args: EventArgs<E>
): void {
  if (analyticsEnabled) {
    posthog.capture(event, args[0])
  } else if (!analyticsSettled && pendingEvents.length < MAX_PENDING_EVENTS) {
    pendingEvents.push([event, args[0]])
  }
}

/** `trackEvent` once per app session for each key, such as a step or a prompt state. */
const trackedOnce = new Set<string>()
export function trackEventOnce<E extends RdstEvent>(
  key: string,
  event: E,
  ...args: EventArgs<E>
): void {
  if (trackedOnce.has(key)) return
  trackedOnce.add(key)
  trackEvent(event, ...args)
}

/**
 * The agent attempt a prompt's nonce names, as the server derives it: the first 16 hex
 * digits of a versioned SHA-256 of the nonce. Null without a nonce or without WebCrypto.
 */
export async function agentAttemptId(
  nonce: string | null | undefined
): Promise<string | null> {
  if (!nonce || !globalThis.crypto?.subtle) return null
  try {
    const digest = await globalThis.crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(`rdst-agent-attempt-v1:${nonce}`)
    )
    return Array.from(new Uint8Array(digest).slice(0, 8), (byte) =>
      byte.toString(16).padStart(2, '0')
    ).join('')
  } catch {
    return null
  }
}

/** Whole seconds since `since`, epoch ms. */
export const secondsSince = (since: number) =>
  Math.max(0, Math.round((Date.now() - since) / 1000))

/** `trackEvent` the first time ever on this install, for a property-less milestone. */
export function trackFirstEver(event: 'inbox_first_viewed'): void {
  const key = `rdst_tracked_${event}`
  try {
    if (localStorage.getItem(key)) return
    localStorage.setItem(key, '1')
  } catch {
    return
  }
  trackEvent(event)
}

// Database names, hosts and users seen by this app session, masked wherever a recording
// shows them as text. Capped, since a fleet can list thousands.
const MAX_SENSITIVE_WORDS = 2000
const sensitiveWords = new Set<string>()

/** Masks these values (database names, hosts, users) in session recordings. */
export function maskInReplays(
  values: Iterable<string | null | undefined>
): void {
  for (const value of values) {
    const word = value?.trim()
    if (word && word.length >= 3 && sensitiveWords.size < MAX_SENSITIVE_WORDS)
      sensitiveWords.add(word)
  }
}

// Masked outright: SQL editors and code, monospace text and anything marked `ph-mask`.
const MASK_WHOLE = '.ph-mask, .cm-editor, pre, code, .font-mono'

/** The text a recording keeps: masked whole inside `MASK_WHOLE`, else with known names starred. */
export function maskReplayText(
  text: string,
  element?: HTMLElement | null
): string {
  if (element?.closest?.(MASK_WHOLE)) return text.replace(/\S/g, '*')
  let masked = text
  for (const word of sensitiveWords)
    if (masked.includes(word))
      masked = masked.split(word).join('*'.repeat(word.length))
  return masked
}

/**
 * Session replay: what a recording may hold. Inputs are masked; every text node passes
 * through `maskReplayText`, so SQL, connection forms and known database names, hosts and
 * users never reach a recording, and `ph-no-capture` blocks an element outright. Network
 * requests keep their method, status and timing only: bodies, headers and query strings
 * are dropped, since they carry connection details and SQL.
 */
export const REPLAY_PRIVACY = {
  maskAllInputs: true,
  blockClass: 'ph-no-capture',
  maskTextSelector: '*',
  maskTextFn: maskReplayText,
  recordBody: false,
  recordHeaders: false,
  maskCapturedNetworkRequestFn: (request: CapturedNetworkRequest) => ({
    ...request,
    name: request.name.split('?')[0] ?? request.name,
    requestBody: null,
    responseBody: null,
    requestHeaders: {},
    responseHeaders: {},
  }),
} satisfies SessionRecordingOptions

/**
 * Runs once `initAnalytics` settles, whatever it decided: applies the replay privacy
 * settings and sends the events that waited, or drops them when reporting is off.
 */
export function finishAnalyticsSetup(): void {
  analyticsSettled = true
  const waiting = pendingEvents
  pendingEvents = []
  if (!analyticsEnabled) return
  posthog.set_config({ session_recording: REPLAY_PRIVACY })
  for (const [event, properties] of waiting)
    posthog.capture(event, properties as Record<string, unknown> | undefined)
}
