import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installServerSignInRedirect } from './serverSession'

describe('installServerSignInRedirect', () => {
  let meta: HTMLMetaElement
  const assign = vi.fn()
  const realFetch = window.fetch

  beforeEach(() => {
    meta = document.createElement('meta')
    meta.name = 'rdst-runtime'
    meta.content = 'container'
    document.head.appendChild(meta)
    vi.stubGlobal('location', {
      ...window.location,
      assign,
      pathname: '/database',
      search: '',
    })
  })

  afterEach(() => {
    meta.remove()
    window.fetch = realFetch
    assign.mockClear()
    vi.unstubAllGlobals()
  })

  function respond(headers: Record<string, string>) {
    window.fetch = vi.fn(
      async () => new Response('{}', { status: 401, headers })
    )
    installServerSignInRedirect()
  }

  it("sends the browser to sign in when the server's access check refuses", async () => {
    respond({ 'X-RDST-Sign-In': 'required' })
    await window.fetch('/api/monitor/targets')
    expect(assign).toHaveBeenCalledWith('/login?next=%2Fdatabase')
  })

  it("leaves a route's own 401 with the page", async () => {
    respond({})
    const response = await window.fetch('/api/monitor/shop/deep-analysis')
    expect(response.status).toBe(401)
    expect(assign).not.toHaveBeenCalled()
  })
})
