// Fleet Types
// ---------------------------------------------------------------------------
//
// REST-backed shapes come from the generated OpenAPI types. Fleet members
// travel as free-form dicts inside FleetTargetsResponse, so their shape is
// hand-typed here, mirroring FleetService.list_fleet in
// rdst/features/fleet/service.py.

import type { components } from '../lib/api.generated'

export type FleetEvent = components['schemas']['FleetEvent']
export type FleetConnectivityEvent = Extract<
  FleetEvent,
  { type: 'connectivity' }
> & {
  code?: string | null
  password_env?: string | null
}
export type FleetImportProgressEvent = Extract<
  FleetEvent,
  { type: 'import_progress' }
>
export type FleetImportCompleteEvent = Extract<
  FleetEvent,
  { type: 'import_complete' }
>

type GeneratedAuditEvent = components['schemas']['AuditEvent']
type GeneratedAuditStatusEvent = Extract<
  GeneratedAuditEvent,
  { type: 'status' }
>

/**
 * Fleet audits enrich the existing status event with optional target-scoped
 * progress. The generated OpenAPI client can lag the streaming dataclass, so
 * keep this additive contract next to the SSE consumer.
 */
export type AuditEvent =
  | Exclude<GeneratedAuditEvent, { type: 'status' }>
  | (GeneratedAuditStatusEvent & {
      target_name?: string | null
      elapsed_seconds?: number | null
      total_seconds?: number | null
      step?: string | null
    })

export interface FleetMember {
  name: string
  engine: string
  host: string
  port: number
  database: string
  user?: string
  password_env?: string
  has_password?: boolean
  group?: string | null
  tags?: string[]
  instance_class?: string | null
  instance_class_source?: 'aws' | 'estimated' | string | null
  target_type?: string
  region?: string | null
  tls?: boolean
  read_only?: boolean
}

export interface FleetTargets {
  members: FleetMember[]
  groups: string[]
  count: number
}

export type FleetStreamState = 'idle' | 'running' | 'complete' | 'error'

export type FleetSnapshotSummary = components['schemas']['FleetSnapshotSummary']
export type FleetSnapshotListResponse =
  components['schemas']['FleetSnapshotListResponse']
