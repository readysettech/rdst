import { describe, expect, it } from 'vitest'
import {
  classifyError,
  friendlySqlError,
  normalizeHttpError,
  normalizeSseError,
  recoveryFor,
  retryHelps,
  sanitizeWebError,
} from './errorContract'

describe('classifyError', () => {
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

  it('falls back for opaque bodies', () => {
    expect(normalizeHttpError(500, 'boom').message).toContain('500')
  })
})
