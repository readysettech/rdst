import { describe, expect, it } from 'vitest'

import { throwIfNotOk } from './httpError'

describe('throwIfNotOk', () => {
  it('shows a FastAPI detail instead of a bare status', async () => {
    const response = new Response(
      JSON.stringify({
        detail:
          'Remote requests require a same-origin Origin or Referer header',
      }),
      { status: 403, headers: { 'Content-Type': 'application/json' } }
    )

    await expect(
      throwIfNotOk(response, 'Failed to save secret')
    ).rejects.toThrow(
      'Remote requests require a same-origin Origin or Referer header'
    )
  })

  it('keeps a readable upstream message', async () => {
    const response = new Response(
      JSON.stringify({ detail: 'Anthropic reports insufficient credits.' }),
      { status: 402, headers: { 'Content-Type': 'application/json' } }
    )

    await expect(
      throwIfNotOk(response, 'Failed to validate Anthropic key')
    ).rejects.toThrow('Anthropic reports insufficient credits.')
  })
})
