import { isContainerRuntime } from './desktop'

/**
 * A server running in a container signs browsers in with a session cookie.
 * When that session ends (the token changed, the cookie expired), every API
 * call answers 401; send the browser back to the server's sign-in page instead
 * of leaving the app on failed requests.
 */
export function installServerSignInRedirect(): void {
  if (!isContainerRuntime()) return
  const originalFetch = window.fetch.bind(window)
  let redirecting = false
  window.fetch = async (...args: Parameters<typeof fetch>) => {
    const response = await originalFetch(...args)
    // Only the server's own access check sets the header; a route's 401 (such as
    // "sign in to Readyset") stays with the page that asked.
    if (
      response.status === 401 &&
      response.headers.get('X-RDST-Sign-In') === 'required' &&
      !redirecting &&
      isSameOriginApi(args[0])
    ) {
      redirecting = true
      const next = window.location.pathname + window.location.search
      window.location.assign(`/login?next=${encodeURIComponent(next)}`)
    }
    return response
  }
}

function isSameOriginApi(input: RequestInfo | URL): boolean {
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.href
        : input.url
  const url = new URL(raw, window.location.origin)
  return (
    url.origin === window.location.origin && url.pathname.startsWith('/api/')
  )
}
