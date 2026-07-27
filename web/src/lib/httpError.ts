// Shared HTTP failure helpers.
//
// Two shapes, because two clients report failures differently:
//   - `throwIfNotOk` reads the still-unconsumed response body (plain `fetch`).
//   - `throwIfApiError` reads openapi-fetch's already-parsed `error` payload.

/** Throw the response body, or `${ctx}: ${status}` when the body is empty. */
export async function throwIfNotOk(
  response: Response,
  ctx: string
): Promise<void> {
  if (response.ok) return
  const body = await response.text().catch(() => '')
  throw new Error(body || `${ctx}: ${response.status}`)
}

/**
 * Pull the human-readable reason out of an openapi-fetch error payload.
 * FastAPI errors travel as {detail: string | {message, ...}}.
 */
export function extractDetail(error: unknown): string {
  if (!error || typeof error !== 'object') {
    return typeof error === 'string' ? error : ''
  }
  const detail = (error as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string') return message
    return JSON.stringify(detail)
  }
  const message = (error as { message?: unknown }).message
  return typeof message === 'string' ? message : ''
}

/**
 * openapi-fetch parses the response body itself, so on a non-2xx status the
 * parsed payload is returned as `error` (not readable again from `response`).
 * Prefer the backend's reason (409 exists, 422 invalid name, 502 LLM failure,
 * 423 locked target) over a bare status.
 */
export function throwIfApiError(
  response: Response,
  error: unknown,
  ctx: string
): void {
  if (response.ok) return
  const detail = extractDetail(error)
  throw new Error(detail || `${ctx}: ${response.status}`)
}
