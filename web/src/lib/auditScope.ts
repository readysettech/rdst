import type { FleetConnectivityEvent, FleetMember } from '../types/fleet'
import type { FleetAuditRequest } from './useFleet'

// ---------------------------------------------------------------------------
// Health-check scope model
// ---------------------------------------------------------------------------
//
// The Health Check page runs audits at four scopes: one target, an arbitrary
// selection of targets, a named group, or the entire fleet. The scope (and a
// group/target preselection) travels in the URL search params so cross-page
// links from Fleet land preselected and survive reload.

export type AuditScopeKind = 'single' | 'multi' | 'group' | 'fleet'

export interface AuditSearch {
  scope?: AuditScopeKind
  group?: string
  target?: string
  targets?: string
  tab?: 'history'
}

const SCOPES: readonly string[] = ['single', 'multi', 'group', 'fleet']

/**
 * Parse-only search validation for /audit. Never throws: unknown scopes fall
 * back to the default (single) and empty strings drop out so the URL stays
 * clean.
 */
export function parseAuditSearch(search: Record<string, unknown>): AuditSearch {
  const rawScope = typeof search.scope === 'string' ? search.scope : ''
  const group =
    typeof search.group === 'string' && search.group ? search.group : undefined
  const target =
    typeof search.target === 'string' && search.target
      ? search.target
      : undefined
  const targets =
    typeof search.targets === 'string' && search.targets
      ? search.targets
      : undefined
  const tab = search.tab === 'history' ? 'history' : undefined
  return {
    scope: SCOPES.includes(rawScope) ? (rawScope as AuditScopeKind) : undefined,
    group,
    target,
    targets,
    tab,
  }
}

export interface AvailabilitySplit {
  available: FleetMember[]
  unavailable: FleetMember[]
}

/**
 * Partition members by their last known connectivity. Only a settled failed
 * check counts as unavailable — unknown or in-flight statuses stay available
 * so a target is never demoted before it has actually failed a check.
 * Relative order within each partition is preserved.
 */
export function splitByAvailability(
  members: FleetMember[],
  connectivity: Record<string, FleetConnectivityEvent | undefined>
): AvailabilitySplit {
  const available: FleetMember[] = []
  const unavailable: FleetMember[] = []
  for (const member of members) {
    const status = connectivity[member.name]?.status
    if (status && status !== 'ok' && status !== 'checking') {
      unavailable.push(member)
    } else {
      available.push(member)
    }
  }
  return { available, unavailable }
}

/** Translate legacy /audit URL scope params into checkbox preselection. */
export function selectionFromAuditSearch(
  search: AuditSearch,
  members: FleetMember[],
  currentTarget?: string | null
): string[] {
  if (search.target) return [search.target]
  if (search.targets) {
    const requested = new Set(search.targets.split(',').filter(Boolean))
    return members
      .filter((member) => requested.has(member.name))
      .map((member) => member.name)
  }
  if (search.group) {
    return members
      .filter((member) => member.group === search.group)
      .map((member) => member.name)
  }
  if (search.scope === 'fleet') return members.map((member) => member.name)
  // The launcher is intentionally empty on a plain /audit arrival. Explicit
  // links from Fleet still preselect through target/group/targets above.
  void currentTarget
  return []
}

/** Explicit multi-target request used by the unified checkbox picker. */
export function buildTargetSelectionRequest(
  targets: string[],
  durationSeconds?: number
): FleetAuditRequest {
  return {
    targets,
    ...(durationSeconds ? { duration: durationSeconds } : {}),
  }
}

/**
 * Build the POST /api/fleet/audit body for a non-single scope.
 *
 * - group: server resolves membership from the group name.
 * - multi: explicit `targets` selection.
 * - fleet: an explicit `targets` list of reachable members when known-dead
 *   targets are being skipped; an empty body (server audits everything)
 *   when there is nothing to skip or the user opted to include them.
 *
 * `durationSeconds` turns the run into a live capture on every target;
 * omitted means a metrics-only instant snapshot.
 */
export function buildFleetAuditRequest(options: {
  scope: Exclude<AuditScopeKind, 'single'>
  group?: string
  selectedTargets?: string[]
  availableTargets?: string[]
  includeUnavailable?: boolean
  durationSeconds?: number
}): FleetAuditRequest {
  const request: FleetAuditRequest = {}
  if (options.scope === 'group') {
    if (options.group) request.group = options.group
  } else if (options.scope === 'multi') {
    request.targets = options.selectedTargets ?? []
  } else if (!options.includeUnavailable && options.availableTargets) {
    request.targets = options.availableTargets
  }
  if (options.durationSeconds) request.duration = options.durationSeconds
  return request
}

/** Error codes the fleet-audit endpoint emits when a run selects too many targets. */
const TARGET_CAP_CODES = new Set(['too_many_targets', 'FLEET_AUDIT_TARGET_CAP'])

export function isTargetCapError(code: string | undefined): boolean {
  return !!code && TARGET_CAP_CODES.has(code)
}
