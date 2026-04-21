/**
 * Contract canary.
 *
 * This file exists so the typed client (`api` from ./client) is exercised by
 * tsc even before the feature-by-feature migration (B3) lands real consumers.
 * It imports nothing at runtime from this file — it's purely a
 * compile-time probe.
 *
 * The call site below reads fields that the backend's `StatusResponse`
 * model (rdst/shared/api/routes/status.py) guarantees. If the backend
 * renames or removes a field, `gen:api` regenerates the types and tsc
 * fails here — which is exactly what the API contract gate checks.
 *
 * Delete this file once a real feature consumer of `api.GET` / `api.POST`
 * lands and serves the same purpose.
 */

import { api } from './client';

export async function _contractProbe(signal?: AbortSignal) {
  const { data, error } = await api.GET('/api/status', { signal });
  if (error || !data) return null;
  // Reading a required field guaranteed by StatusResponse.
  return { configured: data.configured, targetCount: data.targets.length };
}
