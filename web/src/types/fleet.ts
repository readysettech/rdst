// Fleet Types
// ---------------------------------------------------------------------------
//
// REST-backed shapes come from the generated OpenAPI types. Fleet members
// travel as free-form dicts inside FleetTargetsResponse, so their shape is
// hand-typed here, mirroring FleetService.list_fleet in
// rdst/features/fleet/service.py.

import type { components } from '../lib/api.generated';

export type FleetEvent = components['schemas']['FleetEvent'];
export type FleetConnectivityEvent = Extract<FleetEvent, { type: 'connectivity' }>;
export type FleetImportProgressEvent = Extract<FleetEvent, { type: 'import_progress' }>;
export type FleetImportCompleteEvent = Extract<FleetEvent, { type: 'import_complete' }>;
export type FleetDiscoverEvent = Extract<FleetEvent, { type: 'discover' }>;

export type AuditEvent = components['schemas']['AuditEvent'];

export interface FleetMember {
  name: string;
  engine: string;
  host: string;
  port: number;
  database: string;
  group?: string | null;
  tags?: string[];
  instance_class?: string | null;
  target_type?: string;
  region?: string | null;
}

export interface FleetTargets {
  members: FleetMember[];
  groups: string[];
  count: number;
}

export type FleetStreamState = 'idle' | 'running' | 'complete' | 'error';

export type FleetSnapshotSummary = components['schemas']['FleetSnapshotSummary'];
export type FleetSnapshotListResponse = components['schemas']['FleetSnapshotListResponse'];
export type FleetDiffResponse = components['schemas']['FleetDiffResponse'];
export type FleetDiffEntry = components['schemas']['FleetDiffEntryResponse'];
