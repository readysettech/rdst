// Shared unit-test scaffolding. Import as `@/test-utils`.
//
// Everything here is inert helper code: a React Query harness, Response
// builders for the SSE/JSON fetch stubs, a TanStack Router module mock for
// route tests, and the fleet fixtures that several suites share.

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, type RenderHookResult } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { vi } from 'vitest'

/** A QueryClient that fails fast, so a stubbed rejection surfaces in one tick. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

/** Provider wrapper for `renderHook`, sharing one client across rerenders. */
export function queryClientWrapper(client: QueryClient = createTestQueryClient()) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

/**
 * Render `ui` under a QueryClientProvider. The returned `rerender` re-wraps, so
 * callers can hand it plain elements, and `client` is exposed for seeding.
 */
export function renderWithClient(
  ui: ReactElement,
  client: QueryClient = createTestQueryClient()
) {
  const result = render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>
  )
  return {
    ...result,
    client,
    rerender: (next: ReactElement) =>
      result.rerender(
        <QueryClientProvider client={client}>{next}</QueryClientProvider>
      ),
  }
}

/** A one-shot streaming Response, as the SSE consumers expect from fetch. */
export function sseResponse(payload: string): Response {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

/**
 * Build an SSE payload. The background-run registry pops each event's `type`
 * and promotes it to the SSE `event:` name, so the data never carries a
 * discriminator.
 */
export function sseFrames(
  ...items: Array<[string, Record<string, unknown>]>
): string {
  return items
    .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join('')
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * A fetch stub that holds the SSE connection open forever: it settles only by
 * rejecting once the caller's AbortSignal fires, which is what keeps a
 * background run "in flight" while a test remounts around it.
 */
export function stubAbortableFetch() {
  const fetchMock = vi.fn(
    (_url: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError'))
        )
      })
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/**
 * Drive the shared background-run contract: a run started under one hook
 * instance is still in flight after that instance unmounts, and cancelling from
 * a fresh instance tears it down. `assertRunning` is checked against both
 * instances; the returned value is the second instance after cancellation.
 */
export async function runSurvivesRemountAndCancel<
  T extends { cancel: () => void },
>({
  renderRunHook,
  start,
  assertRunning,
}: {
  renderRunHook: () => RenderHookResult<T, unknown>
  start: (hook: T) => void
  assertRunning: (hook: T) => void
}): Promise<T> {
  stubAbortableFetch()

  const first = renderRunHook()
  await act(async () => {
    start(first.result.current)
  })
  assertRunning(first.result.current)

  first.unmount()
  const second = renderRunHook()
  assertRunning(second.result.current)

  act(() => second.result.current.cancel())
  return second.result.current
}

/** Anchor-only stand-in for the router's Link, which needs no router context. */
export function LinkStub({
  to,
  children,
  ...rest
}: {
  to?: string
  children?: ReactNode
} & Record<string, unknown>) {
  return (
    <a href={to} {...rest}>
      {children}
    </a>
  )
}

/**
 * Module mock for `@tanstack/react-router` in route tests: `createFileRoute`
 * hands the options object straight back so the loader/component stay callable,
 * and autoCodeSplitting's `lazyRouteComponent` rewrite resolves to identity.
 */
export function fileRouteModuleMock(
  extra: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    createFileRoute: () => (options: unknown) => options,
    lazyRouteComponent: (loader: unknown) => loader,
    Link: LinkStub,
    ...extra,
  }
}

export interface TestTarget {
  name: string
  engine: string
  host: string
  port: number
  database: string
  user: string
  password_env: string
  has_password: boolean
  [key: string]: unknown
}

/** A connectable PostgreSQL fleet member; override only what a test asserts. */
export function makeTarget(overrides: Partial<TestTarget> = {}): TestTarget {
  const name = (overrides.name as string) ?? 'orders'
  return {
    name,
    engine: 'postgresql',
    host: `${name}.test`,
    port: 5432,
    database: name,
    user: `${name}_user`,
    password_env: `RDST_${name.toUpperCase()}_PASSWORD`,
    has_password: false,
    ...overrides,
  }
}

/** Return value for a mocked `useFleetStatus` — idle with no results. */
export function fleetStatusStub(overrides: Record<string, unknown> = {}) {
  return {
    check: overrides.check ?? (() => Promise.resolve()),
    state: 'complete',
    results: {},
    error: undefined,
    reset: () => {},
    ...overrides,
  }
}
