import { useCallback, useRef, useState, useSyncExternalStore } from 'react'
import type { AuditReport } from '../types/audit'
import type {
  AuditEvent,
  FleetConnectivityEvent,
  FleetEvent,
  FleetImportCompleteEvent,
  FleetImportProgressEvent,
  FleetMember,
  FleetSnapshotListResponse,
  FleetStreamState,
  FleetTargets,
} from '../types/fleet'
import {
  beginAuditSession,
  completeAuditSession,
  finishAuditSession,
  updateAuditSession,
} from './auditSession'
import { cancelBackgroundRun, startFleetAuditRun } from './backgroundRuns'
import { api } from './client'
import { throwIfNotOk } from './httpError'
import { consumeSseResponse } from './sseReader'

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function fetchFleetTargets(group?: string): Promise<FleetTargets> {
  const { data, response } = await api.GET('/api/fleet/targets', {
    params: { query: group ? { group } : {} },
  })
  await throwIfNotOk(response, 'Failed to fetch fleet targets')
  if (!data) throw new Error('Missing response body')
  // Members travel as free-form dicts in the OpenAPI schema; FleetMember
  // narrows them to the shape FleetService.list_fleet actually emits.
  return data as unknown as FleetTargets
}

export async function fetchFleetSnapshots(): Promise<FleetSnapshotListResponse> {
  const { data, response } = await api.GET('/api/fleet/snapshots')
  await throwIfNotOk(response, 'Failed to fetch fleet snapshots')
  if (!data) throw new Error('Missing response body')
  return data
}

// Snapshot detail travels as a free-form dict in the OpenAPI schema; these
// interfaces narrow the fields the fleet page reads, mirroring the payload
// FleetService writes for a saved fleet audit.
export interface FleetSnapshotResult extends AuditReport {
  target_name: string
}

export interface FleetSnapshotDetail {
  snapshot_id: string
  name: string
  created_at: string
  targets_audited: number
  targets_failed?: number
  total_monthly_cost_usd?: number | null
  potential_savings_usd?: number | null
  avg_cache_opportunity?: number | null
  fleet_insights?: Record<string, unknown> | null
  results?: FleetSnapshotResult[]
}

export async function fetchFleetSnapshotDetail(
  snapshotId: string
): Promise<FleetSnapshotDetail> {
  const { data, response } = await api.GET(
    '/api/fleet/snapshots/{snapshot_id}',
    {
      params: { path: { snapshot_id: snapshotId } },
    }
  )
  await throwIfNotOk(response, 'Failed to fetch snapshot')
  if (!data) throw new Error('Missing response body')
  return data as unknown as FleetSnapshotDetail
}

// Local AWS credential state for the discovery UI (GET /api/providers/aws-status).
// Untyped by the generated client until gen:api runs.
export interface FleetAwsStatus {
  has_credentials: boolean
  method: string | null
  identity_arn: string | null
  account: string | null
  active_profile: string | null
  available_profiles: string[]
  region: string | null
}

export async function fetchFleetAwsStatus(
  profile?: string
): Promise<FleetAwsStatus> {
  // Pass the user's selected profile: a fresh SSO session lives on that
  // profile and the default credential chain knows nothing about it.
  const query = profile ? `?profile=${encodeURIComponent(profile)}` : ''
  const response = await fetch(`/api/providers/aws-status${query}`)
  await throwIfNotOk(response, 'Failed to check AWS credentials')
  return (await response.json()) as FleetAwsStatus
}

// Discovery preview + selective add (POST /api/providers/discover-preview and
// /api/providers/bulk-add). Hand-typed until gen:api runs.
export interface DiscoveredFleetMember {
  name: string
  engine: string
  host: string
  port: number
  database: string
  user: string
  password_env: string
  group: string | null
  tags: string[]
  instance_class: string | null
  region: string | null
  already_exists: boolean
}

// Regions and profile belong to AWS alone; the account providers discover
// whatever databases the connected account can see.
export type FleetDiscoverInput =
  | { provider?: 'aws'; regions: string[]; profile?: string }
  | { provider: 'supabase' }
  | { provider: 'neon' }
  | { provider: 'digitalocean' }

export async function fetchFleetDiscoverPreview(
  input: FleetDiscoverInput
): Promise<{ members: DiscoveredFleetMember[]; errors: string[] }> {
  const response = await fetch('/api/providers/discover-preview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(input),
  })
  await throwIfNotOk(response, 'Discovery failed')
  return (await response.json()) as {
    members: DiscoveredFleetMember[]
    errors: string[]
  }
}

export async function bulkAddFleetTargets(
  members: DiscoveredFleetMember[]
): Promise<{ imported: number; skipped: number; target_names: string[] }> {
  const response = await fetch('/api/providers/bulk-add', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ members }),
  })
  await throwIfNotOk(response, 'Adding targets failed')
  return (await response.json()) as {
    imported: number
    skipped: number
    target_names: string[]
  }
}

export interface FleetAwsLoginStart {
  login_id: string
  state: 'started' | 'already_signed_in'
  detail: string
}

export interface FleetAwsLoginStatus {
  state: 'running' | 'success' | 'failed' | 'timeout'
  detail: string
  verification_url?: string | null
  fallback_command?: string | null
}

export class FleetAwsLoginError extends Error {
  code?: string
  fallbackCommand?: string
  constructor(message: string, code?: string, fallbackCommand?: string) {
    super(message)
    this.code = code
    this.fallbackCommand = fallbackCommand
  }
}

async function readJson(response: Response): Promise<Record<string, unknown>> {
  return (await response.json().catch(() => ({}))) as Record<string, unknown>
}

export async function startFleetAwsLogin(
  profile: string
): Promise<FleetAwsLoginStart> {
  const response = await fetch('/api/providers/aws-login', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ profile }),
  })
  const body = await readJson(response)
  if (!response.ok) {
    throw new FleetAwsLoginError(
      String(body.detail || body.message || 'Could not start AWS sign-in.'),
      typeof body.code === 'string' ? body.code : undefined,
      typeof body.fallback_command === 'string'
        ? body.fallback_command
        : undefined
    )
  }
  return body as unknown as FleetAwsLoginStart
}

export async function fleetAwsLogout(): Promise<void> {
  const response = await fetch('/api/providers/aws-logout', { method: 'POST' })
  if (!response.ok) {
    const body = await readJson(response)
    throw new FleetAwsLoginError(
      String(body.detail || 'Could not sign out of AWS.')
    )
  }
}

export async function fetchFleetAwsLogin(
  loginId: string
): Promise<FleetAwsLoginStatus> {
  const response = await fetch(
    `/api/providers/aws-login/${encodeURIComponent(loginId)}`
  )
  const body = await readJson(response)
  if (!response.ok)
    throw new FleetAwsLoginError(
      String(body.detail || 'Could not check AWS sign-in.')
    )
  return body as unknown as FleetAwsLoginStatus
}

export interface FleetAwsProfileInput {
  name: string
  sso_start_url: string
  sso_region: string
  sso_account_id: string
  sso_role_name: string
  region: string
}

export async function createFleetAwsProfile(
  input: FleetAwsProfileInput
): Promise<{ created: boolean; profile: string }> {
  const response = await fetch('/api/providers/aws-profiles', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(input),
  })
  const body = await readJson(response)
  if (!response.ok)
    throw new FleetAwsLoginError(
      String(body.detail || body.message || 'Could not create the AWS profile.')
    )
  return body as unknown as { created: boolean; profile: string }
}

// Guided SSO sign-in: authorize a token from a start URL + region alone, then
// let AWS enumerate the accounts and roles the user can actually assume, so
// nothing is hand-typed. The login is polled through the shared
// fetchFleetAwsLogin endpoint.
export interface FleetAwsSsoLoginStart {
  login_id: string | null
  state: 'started' | 'already_signed_in'
  detail: string
}

export interface FleetAwsSsoAccount {
  account_id: string
  account_name: string
}

export async function startFleetAwsSsoLogin(input: {
  start_url: string
  region: string
  session_name?: string
}): Promise<FleetAwsSsoLoginStart> {
  const response = await fetch('/api/providers/aws-sso-login', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(input),
  })
  const body = await readJson(response)
  if (!response.ok)
    throw new FleetAwsLoginError(
      String(body.detail || body.message || 'Could not start AWS sign-in.'),
      typeof body.code === 'string' ? body.code : undefined,
      typeof body.fallback_command === 'string'
        ? body.fallback_command
        : undefined
    )
  return body as unknown as FleetAwsSsoLoginStart
}

export async function fetchFleetAwsSsoAccounts(
  startUrl: string
): Promise<{ accounts: FleetAwsSsoAccount[]; error: string | null }> {
  const response = await fetch(
    `/api/providers/aws-sso-accounts?start_url=${encodeURIComponent(startUrl)}`
  )
  const body = await readJson(response)
  if (!response.ok)
    throw new FleetAwsLoginError(
      String(body.detail || body.message || 'Could not list AWS accounts.'),
      typeof body.code === 'string' ? body.code : undefined
    )
  return body as unknown as {
    accounts: FleetAwsSsoAccount[]
    error: string | null
  }
}

export async function fetchFleetAwsSsoRoles(
  startUrl: string,
  accountId: string
): Promise<{ roles: string[]; error: string | null }> {
  const response = await fetch(
    `/api/providers/aws-sso-roles?start_url=${encodeURIComponent(
      startUrl
    )}&account_id=${encodeURIComponent(accountId)}`
  )
  const body = await readJson(response)
  if (!response.ok)
    throw new FleetAwsLoginError(
      String(body.detail || body.message || 'Could not list AWS roles.'),
      typeof body.code === 'string' ? body.code : undefined
    )
  return body as unknown as { roles: string[]; error: string | null }
}

export interface FleetAwsSsoFinalizeInput {
  name: string
  start_url: string
  region: string
  account_id: string
  role_name: string
}

export async function finalizeFleetAwsSsoProfile(
  input: FleetAwsSsoFinalizeInput
): Promise<{ created: boolean; profile: string; detail?: string }> {
  const response = await fetch('/api/providers/aws-sso-finalize', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(input),
  })
  const body = await readJson(response)
  if (!response.ok)
    throw new FleetAwsLoginError(
      String(body.detail || body.message || 'Could not create the AWS profile.'),
      typeof body.code === 'string' ? body.code : undefined
    )
  return body as unknown as { created: boolean; profile: string; detail?: string }
}

// ---------------------------------------------------------------------------
// Account providers (Supabase, Neon, DigitalOcean)
//
// Every account provider speaks the same four-endpoint dialect under
// /api/fleet/<slug>-<action>, so one client factory covers all of them. Only
// the credential endpoint is named per provider, and only the two providers
// that accept a pasted token have one.
// ---------------------------------------------------------------------------

export type FleetProviderSlug = 'supabase' | 'neon' | 'digitalocean'

export class FleetProviderError extends Error {
  code?: string
  constructor(message: string, code?: string) {
    super(message)
    this.code = code
  }
}

export interface FleetProviderLoginStart {
  login_id: string
  authorize_url: string
}

export interface FleetProviderLoginStatus {
  state: 'running' | 'success' | 'failed'
  detail?: string
}

export interface FleetSupabaseOrganization {
  slug: string
  name: string
}

export interface FleetSupabaseStatus {
  connected: boolean
  method: 'oauth' | null
  detail: string | null
  organizations?: FleetSupabaseOrganization[]
}

// Neon authenticates with an API key alone, so there is no browser sign-in to
// start or poll.
export interface FleetNeonStatus {
  connected: boolean
  method: 'api_key' | null
  detail: string | null
}

// DigitalOcean authenticates through the Readyset OAuth broker alone, so there
// is no token field to fall back to.
export interface FleetDigitaloceanStatus {
  connected: boolean
  method: 'oauth' | null
  detail: string | null
}

async function getFleetJson<T>(
  path: string,
  fallbackMessage: string
): Promise<T> {
  const response = await fetch(path)
  const body = await readJson(response)
  if (!response.ok)
    throw new FleetProviderError(
      String(body.detail || body.message || fallbackMessage),
      typeof body.code === 'string' ? body.code : undefined
    )
  return body as T
}

async function postFleetJson<T>(
  path: string,
  body: unknown,
  fallbackMessage: string
): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    ...(body === undefined
      ? {}
      : {
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        }),
  })
  const parsed = await readJson(response)
  if (!response.ok)
    throw new FleetProviderError(
      String(parsed.detail || parsed.message || fallbackMessage),
      typeof parsed.code === 'string' ? parsed.code : undefined
    )
  return parsed as T
}

interface FleetProviderSpec {
  label: string
  /** Credential endpoint, for the providers that accept a pasted token. */
  tokenPath?: string
  tokenFallback?: string
}

const FLEET_PROVIDERS: Record<FleetProviderSlug, FleetProviderSpec> = {
  supabase: { label: 'Supabase' },
  neon: {
    label: 'Neon',
    tokenPath: '/api/providers/neon-key',
    tokenFallback: 'Could not save the API key.',
  },
  digitalocean: { label: 'DigitalOcean' },
}

export interface FleetProviderClient<S> {
  fetchStatus: () => Promise<S>
  startLogin: () => Promise<FleetProviderLoginStart>
  pollLogin: (loginId: string) => Promise<FleetProviderLoginStatus>
  logout: () => Promise<void>
  /** Present only where a pasted token is a credential path. */
  setToken?: (token: string) => Promise<void>
}

export function providerFleetClient<S>(
  slug: FleetProviderSlug
): FleetProviderClient<S> {
  const { label, tokenPath, tokenFallback } = FLEET_PROVIDERS[slug]
  return {
    fetchStatus: async () => {
      const response = await fetch(`/api/providers/${slug}-status`)
      await throwIfNotOk(response, `Failed to check the ${label} connection`)
      return (await response.json()) as S
    },
    startLogin: () =>
      postFleetJson<FleetProviderLoginStart>(
        `/api/providers/${slug}-login`,
        undefined,
        `Could not start the ${label} sign-in.`
      ),
    pollLogin: (loginId: string) =>
      getFleetJson<FleetProviderLoginStatus>(
        `/api/providers/${slug}-login/${encodeURIComponent(loginId)}`,
        `Could not check the ${label} sign-in.`
      ),
    logout: async () => {
      await postFleetJson(
        `/api/providers/${slug}-logout`,
        undefined,
        `Could not sign out of ${label}.`
      )
    },
    ...(tokenPath
      ? {
          setToken: async (token: string) => {
            await postFleetJson(tokenPath, { token }, tokenFallback as string)
          },
        }
      : {}),
  }
}

const supabaseClient = providerFleetClient<FleetSupabaseStatus>('supabase')
const neonClient = providerFleetClient<FleetNeonStatus>('neon')
const digitaloceanClient =
  providerFleetClient<FleetDigitaloceanStatus>('digitalocean')

// Neon names a credential endpoint above, so its client always carries
// setToken.
type SetFleetToken = (token: string) => Promise<void>

export const fetchFleetSupabaseStatus = supabaseClient.fetchStatus
export const startFleetSupabaseLogin = supabaseClient.startLogin
export const fetchFleetSupabaseLogin = supabaseClient.pollLogin
export const fleetSupabaseLogout = supabaseClient.logout

export const fetchFleetNeonStatus = neonClient.fetchStatus
export const fleetNeonLogout = neonClient.logout
export const setFleetNeonKey = neonClient.setToken as SetFleetToken

export const fetchFleetDigitaloceanStatus = digitaloceanClient.fetchStatus
export const startFleetDigitaloceanLogin = digitaloceanClient.startLogin
export const fetchFleetDigitaloceanLogin = digitaloceanClient.pollLogin
export const fleetDigitaloceanLogout = digitaloceanClient.logout

export async function updateFleetTargetGroup(
  name: string,
  group: string | null
): Promise<void> {
  const response = await fetch(
    `/api/fleet/targets/${encodeURIComponent(name)}`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ group }),
    }
  )
  await throwIfNotOk(response, 'Failed to update target group')
}

export async function updateFleetTargetCredentials(
  member: FleetMember,
  user: string,
  passwordEnv: string
): Promise<void> {
  const response = await fetch(
    `/api/configure/targets/${encodeURIComponent(member.name)}`,
    {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        target: {
          engine: member.engine,
          host: member.host,
          port: member.port,
          database: member.database,
          user,
          password_env: passwordEnv,
          tls: member.tls ?? false,
          read_only: member.read_only ?? false,
        },
      }),
    }
  )
  const body = await readJson(response)
  if (!response.ok || body.success === false) {
    throw new Error(
      String(body.message || body.detail || `Failed to update ${member.name}`)
    )
  }
}

// ---------------------------------------------------------------------------
// Shared SSE consumption
// ---------------------------------------------------------------------------

async function consumeFleetStream<E = FleetEvent>(
  response: Response,
  onEvent: (event: E) => void
): Promise<void> {
  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`HTTP error ${response.status}: ${errorText}`)
  }
  if (!response.body) throw new Error('No response body')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || !trimmed.startsWith('data:')) continue
      const dataStr = trimmed.substring(5).trim()
      try {
        onEvent(JSON.parse(dataStr) as E)
      } catch (e) {
        if (!(e instanceof SyntaxError)) throw e
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Connectivity check hook (SSE)
// ---------------------------------------------------------------------------

interface UseFleetStatusReturn {
  check: (group?: string, targets?: string[]) => Promise<void>
  state: FleetStreamState
  results: Record<string, FleetConnectivityEvent>
  error: string | undefined
  reset: () => void
}

export function useFleetStatus(): UseFleetStatusReturn {
  const [state, setState] = useState<FleetStreamState>('idle')
  const [results, setResults] = useState<
    Record<string, FleetConnectivityEvent>
  >({})
  const [error, setError] = useState<string | undefined>(undefined)
  const abortControllerRef = useRef<AbortController | null>(null)

  const reset = useCallback(() => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    setState('idle')
    setResults({})
    setError(undefined)
  }, [])

  const check = useCallback(async (group?: string, targets?: string[]) => {
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller

    setState('running')
    setResults((current) => {
      if (!targets?.length) return {}
      const selected = new Set(targets)
      return Object.fromEntries(
        Object.entries(current).filter(([name]) => !selected.has(name))
      )
    })
    setError(undefined)

    try {
      const params = new URLSearchParams()
      if (group) params.set('group', group)
      for (const target of targets ?? []) params.append('targets', target)
      const query = params.size > 0 ? `?${params.toString()}` : ''
      const response = await fetch(`/api/fleet/status${query}`, {
        signal: controller.signal,
      })
      await consumeFleetStream(response, (event) => {
        if (event.type === 'connectivity') {
          setResults((prev) => ({ ...prev, [event.target_name]: event }))
        } else if (event.type === 'error') {
          setError(event.message)
        }
      })
      setState((prev) => (prev === 'running' ? 'complete' : prev))
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      setError(err instanceof Error ? err.message : 'An error occurred')
      setState('error')
    } finally {
      abortControllerRef.current = null
    }
  }, [])

  return { check, state, results, error, reset }
}

// ---------------------------------------------------------------------------
// Import / discover hooks (SSE)
//
// CSV import POSTs a JSON body and streams the FleetEvent union: per-instance
// `import_progress`, a terminal `import_complete`, and `error`.
// ---------------------------------------------------------------------------

interface UseFleetStreamReturn {
  run: (body: unknown) => Promise<FleetImportCompleteEvent | undefined>
  state: FleetStreamState
  progress: FleetImportProgressEvent[]
  result: FleetImportCompleteEvent | undefined
  errors: string[]
  reset: () => void
}

function useFleetPostStream(url: string): UseFleetStreamReturn {
  const [state, setState] = useState<FleetStreamState>('idle')
  const [progress, setProgress] = useState<FleetImportProgressEvent[]>([])
  const [result, setResult] = useState<FleetImportCompleteEvent | undefined>(
    undefined
  )
  const [errors, setErrors] = useState<string[]>([])
  const abortControllerRef = useRef<AbortController | null>(null)

  const reset = useCallback(() => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    setState('idle')
    setProgress([])
    setResult(undefined)
    setErrors([])
  }, [])

  const run = useCallback(
    async (body: unknown) => {
      abortControllerRef.current?.abort()
      const controller = new AbortController()
      abortControllerRef.current = controller

      setState('running')
      setProgress([])
      setResult(undefined)
      setErrors([])

      let completion: FleetImportCompleteEvent | undefined
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        })
        await consumeFleetStream(response, (event) => {
          switch (event.type) {
            case 'import_progress':
              setProgress((prev) => [...prev, event])
              break
            case 'import_complete':
              completion = event
              setResult(event)
              setState(event.success ? 'complete' : 'error')
              break
            case 'error':
              setErrors((prev) => [...prev, event.message])
              setState('error')
              break
            default:
              break
          }
        })
        setState((prev) => (prev === 'running' ? 'complete' : prev))
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return undefined
        setErrors((prev) => [
          ...prev,
          err instanceof Error ? err.message : 'An error occurred',
        ])
        setState('error')
      } finally {
        abortControllerRef.current = null
      }
      return completion
    },
    [url]
  )

  return { run, state, progress, result, errors, reset }
}

export interface FleetImportRequest {
  // Server-local path or raw CSV text from the browser file picker; provide
  // exactly one.
  csv_file?: string
  csv_content?: string
  password_env?: string
  group?: string
  tags?: string[]
  dry_run?: boolean
}

interface UseFleetImportReturn {
  runImport: (
    request: FleetImportRequest
  ) => Promise<FleetImportCompleteEvent | undefined>
  state: FleetStreamState
  progress: FleetImportProgressEvent[]
  result: FleetImportCompleteEvent | undefined
  errors: string[]
  reset: () => void
}

export function useFleetImport(): UseFleetImportReturn {
  const { run, state, progress, result, errors, reset } =
    useFleetPostStream('/api/fleet/import')
  const runImport = useCallback(
    (request: FleetImportRequest) => run(request),
    [run]
  )
  return { runImport, state, progress, result, errors, reset }
}

// ---------------------------------------------------------------------------
// Fleet audit hook (SSE)
// ---------------------------------------------------------------------------

export interface FleetAuditRequest {
  group?: string
  tag?: string
  /** Explicit target selection; mutually exclusive with group/tag. */
  targets?: string[]
  insights?: boolean
  save?: boolean
  save_name?: string
  /** Live-capture window per target in seconds; absent = metrics-only audit. */
  duration?: number
}

export type FleetAuditTargetStatus = 'pending' | 'running' | 'done' | 'error'

export interface FleetAuditTargetState {
  status: FleetAuditTargetStatus
  index?: number
  phase?: string
  phaseStartedAt?: number
  statusMessage?: string
  startedAt?: number
  captureStartedAt?: number
  captureElapsedSeconds?: number
  captureTotalSeconds?: number
  benchmarkStartedAt?: number
  benchmarkStep?: string
  verdict?: string
  cacheScore?: number
  error?: string
  notice?: string
}

export interface FleetAuditSummary {
  targets_audited?: number
  successes?: number
  failures?: number
  fleet_insights?: Record<string, unknown> | null
}

interface UseFleetAuditReturn {
  runAudit: (request?: FleetAuditRequest) => Promise<void>
  state: FleetStreamState
  targets: Record<string, FleetAuditTargetState>
  phase: string | undefined
  statusMessage: string | undefined
  summary: FleetAuditSummary | undefined
  snapshotId: string | undefined
  error: string | undefined
  /** Machine-readable code from the SSE error event (e.g. the target cap). */
  errorCode: string | undefined
  running: boolean
  cancel: () => void
  reset: () => void
}

interface FleetAuditSnapshot {
  state: FleetStreamState
  targets: Record<string, FleetAuditTargetState>
  phase: string | undefined
  statusMessage: string | undefined
  summary: FleetAuditSummary | undefined
  snapshotId: string | undefined
  error: string | undefined
  errorCode: string | undefined
}

const EMPTY_FLEET_AUDIT: FleetAuditSnapshot = {
  state: 'idle',
  targets: {},
  phase: undefined,
  statusMessage: undefined,
  summary: undefined,
  snapshotId: undefined,
  error: undefined,
  errorCode: undefined,
}
let fleetAuditSnapshot = EMPTY_FLEET_AUDIT
const fleetAuditListeners = new Set<() => void>()
let fleetAuditController: AbortController | null = null
let fleetAuditSessionId: number | null = null
let fleetAuditRunId: string | null = null

function setFleetAuditSnapshot(
  update:
    | Partial<FleetAuditSnapshot>
    | ((current: FleetAuditSnapshot) => Partial<FleetAuditSnapshot>)
) {
  const patch =
    typeof update === 'function' ? update(fleetAuditSnapshot) : update
  fleetAuditSnapshot = { ...fleetAuditSnapshot, ...patch }
  fleetAuditListeners.forEach((listener) => listener())
}

function subscribeFleetAudit(listener: () => void) {
  fleetAuditListeners.add(listener)
  return () => fleetAuditListeners.delete(listener)
}

function cancelFleetAudit() {
  // Cancelling the background run is what releases the database connection
  // every target in the fleet holds for the length of the run.
  fleetAuditController?.abort()
  fleetAuditController = null
  if (fleetAuditRunId !== null) void cancelBackgroundRun(fleetAuditRunId)
  fleetAuditRunId = null
  setFleetAuditSnapshot({
    state: 'idle',
    phase: undefined,
    statusMessage: undefined,
  })
  if (fleetAuditSessionId !== null) finishAuditSession(fleetAuditSessionId)
  fleetAuditSessionId = null
}

/**
 * Subscribe to a fleet health check's replayable event stream.
 *
 * The registry promotes each event's `type` to the SSE `event:` name, so the
 * frame name is the discriminator that has to be put back before the payload
 * can be read as an AuditEvent.
 */
async function followFleetAudit(
  runId: string,
  sessionId: number,
  initialTargets: string[] = []
): Promise<void> {
  const controller = new AbortController()
  fleetAuditController = controller
  fleetAuditRunId = runId
  updateAuditSession(sessionId, { runId })
  const sessionTargetNames = new Set(initialTargets)
  let terminal = false

  try {
    const response = await fetch(`/api/runs/${runId}/events?after_seq=0`, {
      signal: controller.signal,
    })
    if (!response.ok) {
      throw new Error(`HTTP error ${response.status}: ${await response.text()}`)
    }
    await consumeSseResponse(response, (name, data) => {
      const payload = (data ?? {}) as Record<string, unknown>
      if (name === 'run_end') {
        // The registry's terminal record. A run that ended without its own
        // completion event must not leave the session lock engaged.
        if (terminal) return
        terminal = true
        if (payload.status === 'cancelled') {
          setFleetAuditSnapshot({
            state: 'idle',
            phase: undefined,
            statusMessage: undefined,
          })
        } else {
          setFleetAuditSnapshot((current) => ({
            error:
              current.error || 'Fleet health check ended before completing',
            state: 'error',
          }))
        }
        finishAuditSession(sessionId)
        return
      }

      const event = { ...payload, type: name } as unknown as AuditEvent
      switch (event.type) {
        case 'status':
          setFleetAuditSnapshot((current) => {
            const targetName = event.target_name ?? undefined
            if (!targetName) {
              return {
                phase: event.phase,
                statusMessage: event.message,
              }
            }
            const previous = current.targets[targetName] ?? {
              status: 'pending' as const,
            }
            const now = Date.now()
            const enteringPhase = event.phase !== previous.phase
            const enteringCapture =
              event.phase === 'capture' && previous.phase !== 'capture'
            const enteringBenchmark =
              event.phase === 'readyset' && previous.phase !== 'readyset'
            const benchmarkFailed =
              event.phase === 'readyset' && event.step === 'failed'
            const benchmarkSkipped =
              event.phase === 'readyset' && event.step === 'skipped'
            return {
              phase: event.phase,
              statusMessage: event.message,
              targets: {
                ...current.targets,
                [targetName]: {
                  ...previous,
                  status:
                    previous.status === 'done' || previous.status === 'error'
                      ? previous.status
                      : 'running',
                  phase: event.phase,
                  phaseStartedAt: enteringPhase ? now : previous.phaseStartedAt,
                  statusMessage: event.message,
                  startedAt: previous.startedAt ?? now,
                  ...(enteringCapture ? { captureStartedAt: now } : {}),
                  ...(event.elapsed_seconds != null
                    ? { captureElapsedSeconds: event.elapsed_seconds }
                    : {}),
                  ...(event.total_seconds != null
                    ? { captureTotalSeconds: event.total_seconds }
                    : {}),
                  ...(enteringBenchmark ? { benchmarkStartedAt: now } : {}),
                  ...(event.phase === 'readyset' && event.step
                    ? { benchmarkStep: event.step }
                    : {}),
                  ...(benchmarkFailed || benchmarkSkipped
                    ? { notice: event.message }
                    : {}),
                },
              },
            }
          })
          updateAuditSession(sessionId, {
            statusMessage: event.message,
            phase: event.phase,
          })
          break
        case 'target_start':
          setFleetAuditSnapshot((current) => {
            const now = Date.now()
            return {
              phase: 'collect',
              targets: {
                ...current.targets,
                [event.target_name]: {
                  ...(current.targets[event.target_name] ?? {}),
                  status: 'running',
                  index: event.index,
                  phase: 'collect',
                  phaseStartedAt:
                    current.targets[event.target_name]?.phaseStartedAt ?? now,
                  startedAt:
                    current.targets[event.target_name]?.startedAt ?? now,
                },
              },
            }
          })
          {
            sessionTargetNames.add(event.target_name)
            const names = [...sessionTargetNames]
            updateAuditSession(sessionId, {
              targetNames: names,
              targetLabel:
                names.length === 1 ? names[0] : `${names.length} targets`,
            })
          }
          break
        case 'target_complete': {
          const result = event.result as AuditReport
          setFleetAuditSnapshot((current) => {
            const targets = {
              ...current.targets,
              [event.target_name]: {
                status: 'done' as const,
                verdict: result.sizing?.verdict ?? undefined,
                cacheScore: result.cache_opportunity?.score,
                ...(current.targets[event.target_name]?.notice
                  ? { notice: current.targets[event.target_name].notice }
                  : {}),
              },
            }
            return { targets }
          })
          break
        }
        case 'target_error':
          setFleetAuditSnapshot((current) => {
            const targets = {
              ...current.targets,
              [event.target_name]: {
                status: 'error' as const,
                error: event.error,
              },
            }
            return { targets }
          })
          break
        case 'snapshot_saved':
          setFleetAuditSnapshot({ snapshotId: event.snapshot_id })
          break
        case 'complete': {
          terminal = true
          setFleetAuditSnapshot({
            summary: (event.summary ?? undefined) as
              | FleetAuditSummary
              | undefined,
            snapshotId: event.snapshot_id || fleetAuditSnapshot.snapshotId,
            state: event.success ? 'complete' : 'error',
          })
          const completedId = event.snapshot_id || fleetAuditSnapshot.snapshotId
          if (event.success && completedId) {
            completeAuditSession(sessionId, completedId)
          } else {
            finishAuditSession(sessionId)
          }
          break
        }
        case 'error':
          terminal = true
          setFleetAuditSnapshot({
            error: event.message,
            errorCode: (event as { code?: string }).code,
            state: 'error',
          })
          // The cap error travels with a `code` field that is not part of
          // the typed AuditEvent union yet.
          finishAuditSession(sessionId)
          break
        default:
          break
      }
    })
  } catch (err: unknown) {
    if (err instanceof Error && err.name === 'AbortError') return
    setFleetAuditSnapshot({
      error: err instanceof Error ? err.message : 'An error occurred',
      state: 'error',
    })
    finishAuditSession(sessionId)
  } finally {
    if (fleetAuditController === controller) fleetAuditController = null
    if (fleetAuditSessionId === sessionId) fleetAuditSessionId = null
    if (fleetAuditRunId === runId) fleetAuditRunId = null
  }
}

/**
 * Re-seed the fleet health-check session from a run this browser left in
 * flight, so a reload restores the banner and the one-at-a-time block.
 */
export function resumeFleetAuditSession(run: {
  runId: string
  target: string
  stage: string
  message: string
}): void {
  const sessionId = beginAuditSession({
    kind: 'fleet',
    targetLabel: run.target,
    targetNames: [],
    durationSeconds: 0,
    startedAt: Date.now(),
    phase: run.stage,
    statusMessage: run.message,
    runId: run.runId,
    cancel: cancelFleetAudit,
  })
  if (sessionId === null) return

  fleetAuditSessionId = sessionId
  setFleetAuditSnapshot({
    state: 'running',
    phase: run.stage,
    statusMessage: run.message,
  })
  void followFleetAudit(run.runId, sessionId)
}

export function useFleetAudit(): UseFleetAuditReturn {
  const snapshot = useSyncExternalStore(
    subscribeFleetAudit,
    () => fleetAuditSnapshot,
    () => fleetAuditSnapshot
  )

  const reset = useCallback(() => {
    cancelFleetAudit()
    setFleetAuditSnapshot(EMPTY_FLEET_AUDIT)
  }, [])

  const cancel = useCallback(cancelFleetAudit, [])

  const runAudit = useCallback(async (request?: FleetAuditRequest) => {
    const targetNames = request?.targets ?? []
    const targetLabel = request?.group
      ? `group ${request.group}`
      : targetNames.length === 1
        ? targetNames[0]
        : targetNames.length > 1
          ? `${targetNames.length} targets`
          : 'fleet'
    const sessionId = beginAuditSession({
      kind: 'fleet',
      targetLabel,
      targetNames,
      durationSeconds: request?.duration ?? 0,
      startedAt: Date.now(),
      phase: 'config',
      statusMessage: 'Starting health check...',
      cancel: cancelFleetAudit,
    })
    if (sessionId === null) return
    fleetAuditSessionId = sessionId

    setFleetAuditSnapshot({
      state: 'running',
      targets: Object.fromEntries(
        targetNames.map((name, index) => [
          name,
          { status: 'pending' as const, index },
        ])
      ),
      phase: 'config',
      statusMessage: undefined,
      summary: undefined,
      snapshotId: undefined,
      error: undefined,
      errorCode: undefined,
    })

    // A `reused` response hands back the fleet audit already in flight, which
    // is the same thing this hook wants to follow.
    const started = await startFleetAuditRun(request ?? {})
    if (!started) {
      setFleetAuditSnapshot({
        error: 'Fleet health check could not start',
        state: 'error',
      })
      finishAuditSession(sessionId)
      fleetAuditSessionId = null
      return
    }
    await followFleetAudit(started.runId, sessionId, targetNames)
  }, [])

  return {
    runAudit,
    ...snapshot,
    running: snapshot.state === 'running',
    cancel,
    reset,
  }
}
