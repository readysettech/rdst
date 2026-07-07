import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  checkGuardSql,
  createGuard,
  deleteGuard,
  deriveGuard,
  fetchGuard,
  fetchGuards,
  updateGuard,
} from './useGuards'
import type { GuardDetail } from '../types/guards'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

// openapi-fetch calls fetch(new Request(...)), so the recorded call argument
// is a Request. These helpers read the URL, method, and JSON body off it.
function requestOf(fetchMock: ReturnType<typeof vi.fn>): Request {
  return fetchMock.mock.calls[0][0] as Request
}

async function bodyOf(fetchMock: ReturnType<typeof vi.fn>): Promise<unknown> {
  const text = await requestOf(fetchMock).clone().text()
  return text ? JSON.parse(text) : undefined
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

const sampleDetail: GuardDetail = {
  name: 'pii-guard',
  description: 'Masks PII',
  intent: '',
  derived: false,
  masking: { '*.email': 'email' },
  restrictions: { denied_columns: null, allowed_tables: null, required_filters: null },
  guards: {
    require_where: true,
    require_limit: false,
    no_select_star: false,
    max_tables: null,
    cost_limit: null,
    max_estimated_rows: null,
  },
  limits: { max_rows: 1000, timeout_seconds: 30 },
}

describe('fetchGuards', () => {
  it('returns the guard list payload', async () => {
    const payload = {
      count: 1,
      guards: [
        {
          name: 'pii-guard',
          description: 'Masks PII',
          derived: false,
          mask_count: 1,
          rules: ['require_where'],
          max_rows: 1000,
          created_at: null,
        },
      ],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)))

    const result = await fetchGuards()

    expect(result.count).toBe(1)
    expect(result.guards[0].name).toBe('pii-guard')
    expect(result.guards[0].rules).toEqual(['require_where'])
  })

  it('throws with the backend detail message on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: 'boom' }, 500)),
    )

    await expect(fetchGuards()).rejects.toThrow('boom')
  })
})

describe('fetchGuard', () => {
  it('requests the named guard and returns its detail', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleDetail))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchGuard('pii-guard')

    expect(result.name).toBe('pii-guard')
    expect(result.masking).toEqual({ '*.email': 'email' })
    expect(requestOf(fetchMock).url).toContain('/api/guards/pii-guard')
  })
})

describe('createGuard', () => {
  it('posts the detail and returns the write response', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ success: true, name: 'pii-guard', path: '/x' }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await createGuard(sampleDetail)

    expect(result.success).toBe(true)
    expect(result.name).toBe('pii-guard')
    expect(requestOf(fetchMock).method).toBe('POST')
  })

  it('surfaces a 409 conflict detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: "Guard 'pii-guard' already exists" }, 409)),
    )

    await expect(createGuard(sampleDetail)).rejects.toThrow('already exists')
  })
})

describe('updateGuard', () => {
  it('puts to the named guard endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ success: true, name: 'pii-guard', path: '/x' }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await updateGuard('pii-guard', sampleDetail)

    expect(result.success).toBe(true)
    const request = requestOf(fetchMock)
    expect(request.url).toContain('/api/guards/pii-guard')
    expect(request.method).toBe('PUT')
  })
})

describe('deleteGuard', () => {
  it('deletes the named guard', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ success: true, name: 'pii-guard' }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await deleteGuard('pii-guard')

    expect(result.success).toBe(true)
    const request = requestOf(fetchMock)
    expect(request.url).toContain('/api/guards/pii-guard')
    expect(request.method).toBe('DELETE')
  })
})

describe('deriveGuard', () => {
  it('posts name + intent and returns the previewed detail', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleDetail))
    vi.stubGlobal('fetch', fetchMock)

    const result = await deriveGuard('pii-guard', 'mask emails')

    expect(result.name).toBe('pii-guard')
    expect(requestOf(fetchMock).url).toContain('/api/guards/derive')
    expect(await bodyOf(fetchMock)).toMatchObject({
      name: 'pii-guard',
      intent: 'mask emails',
    })
  })

  it('surfaces an LLM failure detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: 'LLM unavailable' }, 502)),
    )

    await expect(deriveGuard('pii-guard', 'mask emails')).rejects.toThrow('LLM unavailable')
  })
})

describe('checkGuardSql', () => {
  it('posts sql and returns the check response', async () => {
    const payload = {
      guard: 'pii-guard',
      sql: 'SELECT id FROM users',
      passed: false,
      results: [
        {
          guard_name: 'require_where',
          level: 'block',
          message: 'Query must have a WHERE clause',
          passed: false,
          suggestion: 'Add a WHERE clause',
        },
      ],
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload))
    vi.stubGlobal('fetch', fetchMock)

    const result = await checkGuardSql('pii-guard', 'SELECT id FROM users')

    expect(result.passed).toBe(false)
    expect(result.results[0].level).toBe('block')
    expect(requestOf(fetchMock).url).toContain('/api/guards/pii-guard/check')
    expect(await bodyOf(fetchMock)).toMatchObject({ sql: 'SELECT id FROM users' })
  })

  it('passes the target through when provided', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ guard: 'pii-guard', sql: 'SELECT 1', passed: true, results: [] }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await checkGuardSql('pii-guard', 'SELECT 1', 'prod')

    expect(await bodyOf(fetchMock)).toMatchObject({ sql: 'SELECT 1', target: 'prod' })
  })
})
