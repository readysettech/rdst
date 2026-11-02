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
  | 'tour-connect'
  | 'tour-watch'
  | 'tour-inbox'
  | 'tour-agent'

/** The view the add-database dialog opened on. */
export type AddDatabaseEntry = 'form' | 'agent' | 'provider'

/** The screen that asked for a sign-in. */
export type SigninSource = 'monitoring_gate' | 'sign_in_page'

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

/** Where an import of databases comes from: a hosting provider, or a CSV file. */
export type ImportProvider =
  | 'aws'
  | 'supabase'
  | 'neon'
  | 'digitalocean'
  | 'csv'

/** How a provider sign-in proves who the user is. */
export type ProviderSignInMethod = 'oauth' | 'api_key' | 'sso' | 'profile'

/** A control the user used in a provider's sign-in panel or its import screens. */
export type ProviderAction =
  | 'sign_in'
  | 'sign_out'
  | 'retry'
  | 'open_sign_in_link'
  | 'open_verification_page'
  | 'open_provider_console'
  | 'save_key'
  | 'cancel'
  | 'choose_profile'
  | 'add_profile'
  | 'toggle_advanced'
  | 'choose_account'
  | 'choose_role'
  | 'create_profile'
  | 'back'
  | 'toggle_region'
  | 'add_region'
  | 'discover'
  | 'select'
  | 'select_all'
  | 'import'
  | 'choose_csv'
  | 'download_template'
  | 'clear_import'
  | 'skip_to_credentials'
  | 'credentials_later'
  | 'credentials_continue'
  | 'turn_on_monitoring'
  | 'monitoring_later'

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
export type RdstEventProperties = {
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
  /** The occasional Feedback & help invitation (lib/feedbackNudge). */
  feedback_nudge_shown: undefined
  feedback_nudge_clicked: undefined
  feedback_nudge_dismissed: { forever: boolean }
  /** A note sent from the Feedback & help chat; `drafted` when the assistant wrote it. */
  feedback_chat_sent: {
    kind: 'feedback' | 'feature_request' | 'bug'
    drafted: boolean
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
  /**
   * The find-your-database view. Counts and kinds only: never a path, host or name.
   * `run_id` joins the server's `agent_connect_step` events for that scan, search or setup.
   */
  /** `signed_out` lists the agents that reported no sign-in, like `claude:local`. */
  find_view_shown: {
    agents: number
    agent_keys: string
    apps: string
    signed_out: string
    /** The agents that are a desktop app's own copy rather than the user's CLI. */
    app_agents: string
  }
  /** Run it for me on a readiness fix (Neon's CREATE EXTENSION), and how it went. */
  prereq_fix_run: { provider: string; ok: boolean }
  /** The way in the user chose: RDST's scan, a provider's sign-in, the paste prompt, a
   * conversation staged in their Claude app, or the form. */
  find_option_chosen: {
    option:
      | 'scan'
      | 'search_with_agent'
      | 'sign_in'
      | 'paste_prompt'
      | 'open_app'
      | 'manual'
    provider?: string
    app?: string
    /** For a scan: `auto` when RDST looked on opening, `click` when the user asked. */
    started?: 'auto' | 'click'
    /** For a search with an agent: the user's CLI, or the copy a desktop app ships. */
    via?: 'cli' | 'app'
  }
  /** RDST's own scan finished: what each method found, and which couldn't look. */
  find_scan_finished: {
    run_id: string
    /** `done`, or `failed` when the scan itself broke (then `found` says nothing). */
    state: string
    found: number
    /** Of those found: already in RDST, and hosted where a sign-in imports them. */
    already_added: number
    hosted: number
    /** Of those found: running right now (a live container or a server answering on a
     * port), and named in a project's files. */
    running: number
    from_projects: number
    /** How many of the project folders read use at least one database. */
    projects_with_databases: number
    seconds: number
    projects: number
    docker: number
    ports: number
    clients: number
    folders: number
    failed: string
  }
  /** The user started a provider sign-in, or submitted a provider API key. */
  provider_sign_in_started: {
    provider: ImportProvider
    method: ProviderSignInMethod
  }
  /** That sign-in ended. `error_class` is a fixed code or class, never the error's text. */
  provider_sign_in_finished: {
    provider: ImportProvider
    method: ProviderSignInMethod
    ok: boolean
    error_class: string | null
    seconds: number
  }
  /**
   * The user used a control in a provider's panel or import screens. `surface` is the
   * settings card or the add-database panel; `ok` and `error_class` are an action's outcome
   * where it has one (sign out, create profile); `selected` is a toggle's new state; `count`
   * is how many databases or regions the action covered.
   */
  provider_action: {
    provider: ImportProvider
    action: ProviderAction
    surface?: 'card' | 'panel'
    ok?: boolean
    error_class?: string | null
    selected?: boolean
    count?: number
  }
  /** The browser blocked the sign-in tab, so the panel showed a link to open it. */
  provider_sign_in_popup_blocked: { provider: ImportProvider }
  /**
   * Listing a provider's databases finished. `found` counts every database listed and
   * `addable` the ones not yet in RDST; `errors` counts the parts that couldn't be listed.
   * `auto` when RDST listed them on sign-in rather than on a click; `regions` is AWS only.
   */
  provider_discovery_finished: {
    provider: ImportProvider
    ok: boolean
    found: number
    addable: number
    errors: number
    error_class: string | null
    auto: boolean
    regions?: number
    seconds: number
  }
  /** Adding the databases the user picked (or a CSV's rows) finished. */
  provider_import_finished: {
    provider: ImportProvider
    ok: boolean
    selected: number
    imported: number
    skipped: number
    failed: number
    error_class: string | null
  }
  /**
   * The credentials step after an import closed: `connected` once every imported database
   * connected, `later` when the user chose to set them up later. `saved` when they saved
   * credentials at least once.
   */
  provider_credentials_finished: {
    provider: ImportProvider
    outcome: 'connected' | 'later'
    databases: number
    saved: boolean
  }
  /** The user picked one found database. `source` says whether RDST's scan or an agent
   * found it, and `found_by` the kinds of evidence (env, compose, docker, port...). */
  find_database_picked: {
    source: 'scan' | 'search'
    provider: string
    environment: string
    has_secret: boolean
    found_by: string
  }
  /** What the user did with the picked database. */
  find_action: {
    action:
      | 'agent_setup'
      | 'add_self'
      | 'fill_in'
      | 'sign_in'
      | 'look_in_project'
      | 'look_for_more'
      | 'describe'
    agent?: string
    provider?: string
    source: 'scan' | 'search'
    /** For add_self: whether RDST reached the database. */
    ok?: boolean
  }
  /** An agent's search through the user's projects started or ended. */
  find_agent_search: {
    stage: 'started' | 'finished'
    run_id: string
    agent: string
    state?: string
    found?: number
    seconds?: number
    via?: 'cli' | 'app'
  }
  /** An agent's setup of one database ended, and how far it got. */
  find_agent_setup_finished: {
    run_id: string
    agent: string
    state: string
    reached:
      | 'starting'
      | 'adding'
      | 'getting_ready'
      | 'awaiting_user'
      | 'monitoring'
    seconds: number
    tool_steps: number
  }
  /** The agent's conversation was opened in its desktop app. */
  find_continue_in_app: { run_id: string; app: string; kind: string }
  /** The agent was signed out: the user opened its sign-in, then RDST saw it signed in. */
  find_agent_sign_in: { agent: string; via?: 'terminal' | 'app' }
  find_agent_signed_in: { agent: string }
  /** The user picked another agent for the search than the one RDST offered. */
  find_agent_switched: { agent: string }
  /** The user copied the paste prompt. */
  agent_prompt_copied: { attempt_id: string | null }
  /** The find view closed, and where the user stood: what stopped them, if anything. */
  find_view_closed: {
    stage:
      | 'choosing'
      | 'scanning'
      | 'picking'
      | 'agent_searching'
      | 'setting_up'
      | 'awaiting_user'
      | 'setup_done'
      | 'setup_failed'
    found: number
    used_agent: boolean
    seconds_open: number
  }
  /** The user answered the agent's question in RDST, by one of its choices or their own
   * words; the conversation carried on as `run_id`. */
  agent_question_answered: {
    run_id: string
    agent: string
    chose: 'option' | 'other'
    options: number
  }
  /** The "Ask your agent" card on a database's page: connecting the agent to RDST, opening
   * its app (`resumed` when it reopened the conversation that set the database up), or
   * copying the prompt. */
  agent_ask: {
    action: 'connect' | 'open_app' | 'copy'
    agent?: string
    app?: string
    resumed?: boolean
  }
  /** "Fix with Claude" (or Codex) on a database that isn't ready to monitor. */
  agent_help: {
    stage: 'started' | 'finished'
    where: 'turn_on'
    run_id: string
    agent: string
    state?: string
    seconds?: number
    tool_steps?: number
  }
  /** `via` is how the database arrived, or `page` when opened from its page. */
  monitoring_dialog_shown: { via: DatabaseAddVia | 'page' }
  monitoring_prereq_failed: { check: string }
  /** Turning on monitoring asked a signed-out user to sign in for AI. */
  monitoring_signin_gate_shown: { via: DatabaseAddVia | 'page' }
  /**
   * The way past that ask: `sign_in` opened sign-in, `signed_in_after` turned monitoring
   * on once the sign-in finished, `without_ai` turned it on with no AI.
   */
  monitoring_signin_gate_choice: {
    choice: 'sign_in' | 'signed_in_after' | 'without_ai'
  }
  /** `source` names the screen that asked for the sign-in, when one did. */
  signin_started: { method: 'google' | 'email'; source?: SigninSource }
  signin_completed: { source?: SigninSource }
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
