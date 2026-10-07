// Actions and the index plan for one database: what to change, kept current by the monitor.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { components } from '../api.generated'

export type ActionsPage = components['schemas']['ActionsPage']
export type ActionCard = components['schemas']['ActionCard']
export type IndexPlan = components['schemas']['IndexPlan']
export type IndexPlanAdd = components['schemas']['IndexPlanAdd']
export type IndexPlanDrop = components['schemas']['IndexPlanDrop']
export type IndexPlanKeep = components['schemas']['IndexPlanKeep']
export type ActionVerb = 'snooze' | 'dismiss' | 'reopen' | 'check'

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

export const actionKeys = {
  actions: (target: string) => ['monitor', target, 'actions'] as const,
  indexPlan: (target: string, version?: number | null) =>
    version === undefined
      ? (['monitor', target, 'index-plan'] as const)
      : (['monitor', target, 'index-plan', version] as const),
}

export const fetchActions = (target: string) =>
  json<ActionsPage>(`${base(target)}/actions`)

/** Null until the first plan is computed (a 404). */
export async function fetchIndexPlan(
  target: string
): Promise<IndexPlan | null> {
  try {
    return await json<IndexPlan>(`${base(target)}/index-plan`)
  } catch (error) {
    if ((error as { status?: number }).status === 404) return null
    throw error
  }
}

export const postActionVerb = (
  target: string,
  id: number,
  verb: ActionVerb,
  body: { until?: number; reason?: string } = {}
) =>
  json<ActionsPage>(`${base(target)}/actions/${id}/${verb}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

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

export function useActionVerb(target: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      verb,
      until,
      reason,
    }: {
      id: number
      verb: ActionVerb
      until?: number
      reason?: string
    }) => postActionVerb(target, id, verb, { until, reason }),
    onSuccess: (page) => {
      client.setQueryData(actionKeys.actions(target), page)
      void client.invalidateQueries({ queryKey: actionKeys.indexPlan(target) })
    },
  })
}
