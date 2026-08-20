import { describe, expect, it } from 'vitest'
import {
  classifyError,
  friendlySqlError,
  isConnectionFailure,
  isModelContextLimitError,
  isTrialExhaustedError,
  normalizeExplainError,
  normalizeHttpError,
  normalizeSseError,
  recoveryFor,
  retryHelps,
  sanitizeWebError,
} from './errorContract'

describe('classifyError', () => {
  it('detects structured and legacy trial-exhausted failures', () => {
    expect(
      isTrialExhaustedError({
        code: 'TRIAL_EXHAUSTED',
        message: 'credit unavailable',
      })
    ).toBe(true)
    expect(
      isTrialExhaustedError('Your free trial tokens have been used up')
    ).toBe(true)
    expect(isTrialExhaustedError('Anthropic is temporarily unavailable')).toBe(
      false
    )
  })
  it('maps missing-credential failures to user-config', () => {
    expect(
      classifyError({
        code: 'unauthorized',
        message: 'AWS profile not configured',
      })
    ).toBe('user-config')
  })

  it('maps Readyset/Docker failures to local-dependency', () => {
    expect(
      classifyError({
        code: 'error',
        message: 'Readyset container failed to start',
      })
    ).toBe('local-dependency')
  })

  it('classifies a target-DB connection timeout as database, not local-dependency', () => {
    // Regression: generic connectivity words must never route recovery to the
    // local Docker/Readyset setup screen.
    expect(
      classifyError({
        code: 'error',
        message: 'connection to server at "db.internal" timed out',
      })
    ).toBe('database')
    expect(
      classifyError({ code: 'error', message: 'connection refused' })
    ).toBe('database')
  })

  it('maps trial/keyservice failures to rdst-service', () => {
    expect(
      classifyError({ code: 'trial_exhausted', message: 'verify your email' })
    ).toBe('rdst-service')
  })

  it('maps Anthropic failures to provider', () => {
    expect(
      classifyError({ code: 'error', message: 'Claude error: HTTP 401' })
    ).toBe('provider')
    expect(
      classifyError({ code: 'error', message: 'AI request was unauthorized' })
    ).toBe('provider')
  })

  it('recognizes model context limits without treating them as authentication', () => {
    expect(
      isModelContextLimitError({
        code: 'ANTHROPIC_CONTEXT_WINDOW_EXCEEDED',
        message: 'The complete database schema is too large.',
      })
    ).toBe(true)
    expect(
      isModelContextLimitError({
        code: 'error',
        message:
          "The complete database schema could not fit within Anthropic's model context window.",
      })
    ).toBe(true)
    expect(
      isModelContextLimitError({
        code: 'ANTHROPIC_AUTH_INVALID',
        message: 'Anthropic rejected the API key.',
      })
    ).toBe(false)
  })

  it('routes DB credential failures to database, never provider', () => {
    // Regression: a Postgres auth failure must offer "Check connection",
    // not "Fix API key" — the DB-auth signatures beat the provider tokens.
    expect(
      classifyError({
        code: 'error',
        message: 'FATAL: password authentication failed for user "app"',
      })
    ).toBe('database')
    expect(
      classifyError({
        code: 'error',
        message: 'no pg_hba.conf entry for host "10.0.0.5"',
      })
    ).toBe('database')
    expect(
      classifyError({
        code: 'error',
        message: "Access denied for user 'app'@'localhost'",
      })
    ).toBe('database')
  })

  it('never treats digit runs or snake_case identifiers as auth failures', () => {
    // Regression: "1401" must not match the 401 token, and
    // "unauthorized_logs" must not match the unauthorized token.
    expect(
      classifyError({ code: 'error', message: 'query returned 1401 rows' })
    ).toBe('database')
    expect(
      classifyError({
        code: 'error',
        message: 'relation "unauthorized_logs" does not exist',
      })
    ).toBe('database')
  })

  it('defaults SQL/connection failures to database', () => {
    expect(
      classifyError({ code: 'invalid_sql', message: 'syntax error near SELCT' })
    ).toBe('database')
  })
})

describe('recoveryFor / retryHelps', () => {
  it('routes each class to a destination, except valid-negative', () => {
    expect(recoveryFor('user-config')?.to).toBe('/configure')
    expect(recoveryFor('local-dependency')?.to).toBe('/cache')
    expect(recoveryFor('valid-negative')).toBeUndefined()
  })

  it('offers retry only where it can plausibly help', () => {
    expect(retryHelps('local-dependency')).toBe(true)
    expect(retryHelps('rdst-service')).toBe(true)
    expect(retryHelps('database')).toBe(false)
    expect(retryHelps('user-config')).toBe(false)
  })
})

describe('friendlySqlError', () => {
  it('keeps the database syntax text but frames it', () => {
    const msg = friendlySqlError('syntax error at or near "SELCT"')
    expect(msg).toContain('syntax error at or near "SELCT"')
    expect(msg.toLowerCase()).toContain('sql syntax error')
  })

  it('falls back when there is no detail', () => {
    expect(friendlySqlError(undefined)).toMatch(/could not be analyzed/i)
  })

  it('keeps connection details out of the primary message', () => {
    const raw =
      'PostgreSQL EXPLAIN failed: connection to server at "127.0.0.1", port 15434 failed: Connection refused'
    const envelope = normalizeExplainError(raw)

    expect(envelope.code).toBe('database_connection')
    expect(envelope.message).toBe(
      'Could not connect to the database. Check that it is running and reachable, then try again.'
    )
    expect(envelope.message).not.toContain('127.0.0.1')
    expect(envelope.detail).toBe(raw)
  })

  it('classifies actual SQL failures as invalid SQL', () => {
    const envelope = normalizeExplainError('syntax error at or near "SELCT"')

    expect(envelope.code).toBe('invalid_sql')
    expect(envelope.message).toContain('SQL syntax error')
  })
})

describe('sanitizeWebError (P47)', () => {
  it('strips CLI-only remediation that has no web meaning', () => {
    const cleaned = sanitizeWebError(
      "No semantic layer found. Run 'rdst init' to create one."
    )
    expect(cleaned).not.toMatch(/rdst init/i)
    expect(cleaned).toContain('No semantic layer found')
  })

  it('returns a fallback when only CLI text remains', () => {
    expect(sanitizeWebError("Run 'rdst init'.")).toMatch(
      /could not be analyzed/i
    )
  })
})

describe('normalizeSseError / normalizeHttpError', () => {
  it('reads the new envelope shape', () => {
    expect(
      normalizeSseError({ code: 'invalid_sql', message: 'bad', detail: 'x' })
    ).toEqual({
      code: 'invalid_sql',
      message: 'bad',
      detail: 'x',
    })
  })

  it('upgrades a legacy {message} SSE error', () => {
    const env = normalizeSseError({ message: 'no: db error' })
    expect(env.message).toBe('no: db error')
    expect(env.code).toBe('error')
  })

  it('reads a FastAPI {detail} HTTP body', () => {
    const env = normalizeHttpError(404, { detail: 'No such target' })
    expect(env.message).toBe('No such target')
  })

  it('unwraps a categorized FastAPI detail object', () => {
    const env = normalizeHttpError(503, {
      detail: {
        code: 'ssh_key_missing',
        category: 'ssh_key_missing',
        target_name: 'private-db',
        message: 'SSH key not found',
      },
    })
    expect(env).toMatchObject({
      code: 'ssh_key_missing',
      category: 'ssh_key_missing',
      target: 'private-db',
      message: 'SSH key not found',
    })
    expect(isConnectionFailure(env)).toBe(true)
    expect(classifyError(env)).toBe('database')
  })

  it('preserves category and target on a direct HTTP envelope', () => {
    expect(
      normalizeHttpError(502, {
        code: 'provider_ip_blocked',
        category: 'provider_ip_blocked',
        target: 'managed-db',
        message: 'The provider blocked this IP.',
      })
    ).toMatchObject({
      code: 'provider_ip_blocked',
      category: 'provider_ip_blocked',
      target: 'managed-db',
    })
  })

  it('does not confuse AI authentication with SSH authentication', () => {
    expect(
      classifyError({
        code: 'ssh_auth_failed',
        message: 'SSH authentication failed',
      })
    ).toBe('database')
    expect(
      classifyError({
        code: 'auth_invalid',
        message: 'AI authentication failed',
      })
    ).toBe('provider')
  })

  it('falls back for opaque bodies', () => {
    expect(normalizeHttpError(500, 'boom').message).toContain('500')
  })
})
