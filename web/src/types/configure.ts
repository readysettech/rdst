/**
 * Types for Configure feature
 *
 * REST-backed shapes come from the generated OpenAPI types. UI-only state
 * (form data, connection-test shape shaped by SSE payloads) is hand-written.
 */

import type { components } from '../lib/api.generated'

// The "target" rows the /configure/targets list endpoint returns.
//
// TargetSummaryResponse is the declared wire shape. The edit-form pre-fill path
// in routes/configure.tsx also reads a handful of fields that only live on
// TargetDetailResponse (user, password_env, tls, read_only). Those reads yield
// `undefined` for list items today — keeping them here as optional preserves
// that behavior while still pinning the required fields to the generated type.
export type ConfigureTarget = components['schemas']['TargetSummaryResponse'] & {
  user?: string
  password_env?: string
  tls?: boolean
  read_only?: boolean
}

// UI-only form data — not part of the REST contract (password is collected
// for env-var resolution but never sent raw).
export interface ConfigureFormData {
  name: string
  engine: string
  host: string
  port: number
  database: string
  user: string
  password?: string
  password_env?: string
  tls?: boolean
  read_only?: boolean
}

export interface ConfigureTargetDetail extends ConfigureFormData {
  has_password: boolean
  is_default: boolean
}

export type ConfigureState = 'idle' | 'loading' | 'success' | 'error'

// Produced from the SSE `connection_test` event in useConfigure.testConnection.
export interface ConfigureConnectionStatus {
  target: string
  connected: boolean
  error?: string
  engine?: string
  code?: string
  passwordEnv?: string
}
