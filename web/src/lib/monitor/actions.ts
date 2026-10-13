// Actions: one database's actions and index plan, and the global plan and Applied across
// databases, kept current by the monitor. A `ca-*` fixture scenario serves all of them from
// fixtures, verbs included.
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import type { components } from '../api.generated'
import { fixtureOverride } from './contract'
import {
  appliedFixture,
  databaseActionsFixture,
  databaseIndexPlanFixture,
  globalActionsFixture,
  knownFixtureAction,
} from './fixturesActions'
import {
  asCaScenario,
  type CaActionState,
  type CaScenario,
  caFixtures,
} from './fixturesCa'
import { monitorKeys } from './keys'

export type ActionsPage = components['schemas']['ActionsPage']
export type ActionCard = components['schemas']['ActionCard']
export type IndexPlan = components['schemas']['IndexPlan']
export type IndexPlanAdd = components['schemas']['IndexPlanAdd']
export type IndexPlanDrop = components['schemas']['IndexPlanDrop']
export type IndexPlanKeep = components['schemas']['IndexPlanKeep']
export type GlobalActionsPage = components['schemas']['GlobalActionsPage']
export type PlanCard = components['schemas']['PlanCard']
export type PlanQuery = components['schemas']['PlanQuery']
export type PlanTier = NonNullable<PlanCard['priority']>
export type ActionKind = PlanCard['kind']
export type AppliedPage = components['schemas']['AppliedPage']
export type AppliedDay = components['schemas']['AppliedDay']
export type AppliedRow = components['schemas']['AppliedRow']
export type DismissedRow = components['schemas']['DismissedRow']
export type ActionsOpened = components['schemas']['ActionsOpened']
/** The verbs of the global Actions page. */
export type PlanVerb =
  | 'set_aside'
  | 'dismiss'
  | 'did_it'
  | 'undid_it'
  | 'bring_back'
export type ActionVerb = 'snooze' | 'reopen' | 'check' | PlanVerb

const base = (target: string) =>
  `/api/monitor/targets/${encodeURIComponent(target)}`

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      /* keep the generic message */
    }
    throw Object.assign(new Error(message), { status: response.status })
  }
  return response.json() as Promise<T>
}

const dbQuery = (db: string | null) =>
  db ? `?db=${encodeURIComponent(db)}` : ''

export const actionKeys = {
  /** Every key of the global page: the plan and Applied, for every database filter. */
  global: ['monitor', 'actions'] as const,
  plan: (db: string | null) =>
    ['monitor', 'actions', 'plan', db ?? 'all'] as const,
  applied: (db: string | null) =>
    ['monitor', 'actions', 'applied', db ?? 'all'] as const,
  actions: (target: string) => ['monitor', target, 'actions'] as const,
  indexPlan: (target: string, version?: number | null) =>
    version === undefined
      ? (['monitor', target, 'index-plan'] as const)
      : (['monitor', target, 'index-plan', version] as const),
}

// Where fixture verbs moved each action, per scenario, until the page reloads, and the
// reasons given for a set aside, a dismissal or an undo.
const stubStates = new Map<CaScenario, Map<number, CaActionState>>()
const stubReasons = new Map<CaScenario, Map<number, string>>()

/** Tests: forget fixture verbs. */
export function resetActionStubState() {
  stubStates.clear()
  stubReasons.clear()
}

function reasonsFor(scenario: CaScenario): Map<number, string> {
  let reasons = stubReasons.get(scenario)
  if (!reasons) {
    reasons = new Map()
    stubReasons.set(scenario, reasons)
  }
  return reasons
}

function statesFor(scenario: CaScenario): Map<number, CaActionState> {
  let states = stubStates.get(scenario)
  if (!states) {
    states = new Map()
    stubStates.set(scenario, states)
  }
  return states
}

const caScenario = () => asCaScenario(fixtureOverride())

export async function fetchActions(target: string): Promise<ActionsPage> {
  const scenario = caScenario()
  if (scenario)
    return databaseActionsFixture(
      scenario,
      target,
      statesFor(scenario),
      reasonsFor(scenario)
    )
  return json<ActionsPage>(`${base(target)}/actions`)
}

/** Null until the first plan is computed (a 404). */
export async function fetchIndexPlan(
  target: string
): Promise<IndexPlan | null> {
  const scenario = caScenario()
  if (scenario)
    return databaseIndexPlanFixture(scenario, target, statesFor(scenario))
  try {
    return await json<IndexPlan>(`${base(target)}/index-plan`)
  } catch (error) {
    if ((error as { status?: number }).status === 404) return null
    throw error
  }
}

const STUB_STATE: Record<ActionVerb, CaActionState['state']> = {
  snooze: 'snoozed',
  set_aside: 'snoozed',
  dismiss: 'dismissed',
  check: 'applied',
  did_it: 'applied',
  undid_it: 'undone',
  reopen: 'open',
  bring_back: 'open',
}

/**
 * A fixture verb: set aside (snooze) and dismiss close an action, check and did_it mark it
 * applied, undid_it marks it undone, and bring_back (reopen) opens it again.
 */
function stubVerb(
  scenario: CaScenario,
  target: string,
  id: number,
  verb: ActionVerb,
  body: { until?: number; reason?: string }
): ActionsPage {
  const set = caFixtures(scenario)
  const states = statesFor(scenario)
  const reasons = reasonsFor(scenario)
  const page = databaseActionsFixture(scenario, target, states, reasons)
  const known = [...page.actions, ...page.set_aside, ...page.recently_done]
  if (
    !known.some((card) => card.id === id) &&
    !knownFixtureAction(scenario, id)
  )
    throw Object.assign(new Error(`No action ${id} on ${target}`), {
      status: 404,
    })
  const state = STUB_STATE[verb]
  states.set(id, {
    state,
    snoozed_until:
      state === 'snoozed' ? (body.until ?? set.now + 7 * 86400) : null,
    updated_at: set.now,
  })
  if (body.reason) reasons.set(id, body.reason)
  else reasons.delete(id)
  return databaseActionsFixture(scenario, target, states, reasons)
}

export async function postActionVerb(
  target: string,
  id: number,
  verb: ActionVerb,
  body: { until?: number; reason?: string } = {}
): Promise<ActionsPage> {
  const scenario = caScenario()
  if (scenario) return stubVerb(scenario, target, id, verb, body)
  return json<ActionsPage>(`${base(target)}/actions/${id}/${verb}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// The monitor recomputes actions every few minutes; the first plan lands within ten.
export const useActions = (target: string, enabled = true) =>
  useQuery({
    queryKey: actionKeys.actions(target),
    queryFn: () => fetchActions(target),
    enabled: enabled && Boolean(target),
    refetchInterval: (query) => {
      const state = query.state.data?.checkup?.state
      return state === 'first_pass' ? 5_000 : 60_000
    },
  })

/** `version` is the actions' computed_at: a new plan fetches the moment the actions move. */
export const useIndexPlan = (
  target: string,
  enabled = true,
  version?: number | null
) =>
  useQuery({
    queryKey: actionKeys.indexPlan(target, version),
    placeholderData: (previous) => previous,
    queryFn: () => fetchIndexPlan(target),
    enabled: enabled && Boolean(target),
    refetchInterval: 60_000,
  })

/** The page's clock in unix seconds: a redesign fixture runs on its own timeline. */
export function actionsNow(): number {
  const scenario = caScenario()
  return scenario ? caFixtures(scenario).now : Math.floor(Date.now() / 1000)
}

/** The plan across databases, or one database's with `db`. */
export async function fetchGlobalActions(
  db: string | null
): Promise<GlobalActionsPage> {
  const scenario = caScenario()
  if (scenario)
    return globalActionsFixture(
      scenario,
      db,
      statesFor(scenario),
      reasonsFor(scenario)
    )
  return json<GlobalActionsPage>(`/api/monitor/actions${dbQuery(db)}`)
}

/** The changes applied in the last 30 days, with the undone and dismissed ones. */
export async function fetchApplied(db: string | null): Promise<AppliedPage> {
  const scenario = caScenario()
  if (scenario)
    return appliedFixture(
      scenario,
      db,
      statesFor(scenario),
      reasonsFor(scenario)
    )
  return json<AppliedPage>(`/api/monitor/actions/applied${dbQuery(db)}`)
}

/** Actions was opened for a database: its Changes ready event closes. */
export async function postActionsOpened(
  target: string
): Promise<ActionsOpened> {
  if (caScenario()) return { target, closed: false }
  return json<ActionsOpened>(`${base(target)}/actions/opened`, {
    method: 'POST',
  })
}

export const usePlan = (db: string | null) =>
  useQuery({
    queryKey: actionKeys.plan(db),
    queryFn: () => fetchGlobalActions(db),
    placeholderData: (previous) => previous,
    refetchInterval: 60_000,
  })

export const useApplied = (db: string | null) =>
  useQuery({
    queryKey: actionKeys.applied(db),
    queryFn: () => fetchApplied(db),
    placeholderData: (previous) => previous,
    refetchInterval: 60_000,
  })

/** An index a database's plan keeps, with the window its reads were counted over. */
export interface KeptIndex extends IndexPlanKeep {
  database: string
  observed_s: number
}

/** The indexes each database's plan keeps, read only while they are asked for. */
export const useKeptIndexes = (databases: string[], enabled: boolean) =>
  useQueries({
    queries: databases.map((database) => ({
      queryKey: actionKeys.indexPlan(database),
      queryFn: () => fetchIndexPlan(database),
      enabled,
    })),
    combine: (results): KeptIndex[] =>
      results.flatMap((result, index) =>
        (result.data?.keep ?? []).map((keep) => ({
          ...keep,
          database: databases[index],
          observed_s: result.data?.observed_s ?? 0,
        }))
      ),
  })

/** What a verb moves: the global page, the database's own actions, Home and the Inbox. */
function invalidateAfterVerb(
  client: ReturnType<typeof useQueryClient>,
  target: string
) {
  for (const queryKey of [
    actionKeys.global,
    actionKeys.actions(target),
    actionKeys.indexPlan(target),
    monitorKeys.home().slice(0, 2),
    monitorKeys.inboxAll,
  ])
    void client.invalidateQueries({ queryKey })
}

/** A verb on one change of the global page. */
export function usePlanVerb() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      target,
      id,
      verb,
      until,
      reason,
    }: {
      target: string
      id: number
      verb: PlanVerb
      until?: number
      reason?: string
    }) => postActionVerb(target, id, verb, { until, reason }),
    onSuccess: (_page, { target }) => invalidateAfterVerb(client, target),
  })
}

/** Opening Actions for a database closes its Changes ready event in the Inbox. */
export function useActionsOpened() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: postActionsOpened,
    onSuccess: (result) => {
      if (!result.closed) return
      void client.invalidateQueries({ queryKey: monitorKeys.inboxAll })
      void client.invalidateQueries({
        queryKey: monitorKeys.home().slice(0, 2),
      })
    },
  })
}
