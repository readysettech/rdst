// Guard Types
// ---------------------------------------------------------------------------
//
// REST-backed shapes come from the generated OpenAPI types, mirroring the
// GuardConfig / check pipeline in rdst/features/guard/. Guards are policy
// definitions that mask columns, restrict tables/filters, and enforce query
// rules; each can be checked against a SQL statement.

import type { components } from '../lib/api.generated';

export type GuardSummary = components['schemas']['GuardSummary'];
export type GuardListResponse = components['schemas']['GuardListResponse'];
export type GuardDetail = components['schemas']['GuardDetail'];
export type GuardRulesModel = components['schemas']['GuardRulesModel'];
export type GuardLimitsModel = components['schemas']['GuardLimitsModel'];
export type GuardRestrictionsModel = components['schemas']['GuardRestrictionsModel'];
export type GuardWriteResponse = components['schemas']['GuardWriteResponse'];
export type GuardDeriveRequest = components['schemas']['GuardDeriveRequest'];
export type GuardCheckRequest = components['schemas']['GuardCheckRequest'];
export type GuardCheckResponse = components['schemas']['GuardCheckResponse'];
export type GuardCheckResult = components['schemas']['GuardCheckResult'];

// A check result's severity. The backend emits these three levels; anything
// else is treated as informational.
export type GuardCheckLevel = 'block' | 'warn' | 'info';

// Masking pattern types the editor offers. `partial:N` carries an argument, so
// it is stored as a free-form string; the rest are fixed tokens.
export const MASK_TYPES = ['redact', 'email', 'hash'] as const;
export type MaskType = (typeof MASK_TYPES)[number] | `partial:${number}`;
