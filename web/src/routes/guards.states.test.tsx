import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock()
})

const guardsQuery = {
  data: undefined as { guards: unknown[] } | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
}

const idleMutation = { mutateAsync: vi.fn(), isPending: false }

const guardDetail = {
  name: 'pii-mask',
  description: '',
  intent: '',
  derived: false,
  masking: {},
  restrictions: {},
  guards: {},
  limits: { max_rows: 1000, timeout_seconds: 30 },
}

vi.mock('../lib/useGuards', () => ({
  useGuardsList: () => guardsQuery,
  useGuardDetail: () => ({
    data: guardDetail,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useCreateGuard: () => idleMutation,
  useUpdateGuard: () => idleMutation,
  useDeleteGuard: () => idleMutation,
  checkGuardSql: vi.fn(),
  deriveGuard: vi.fn(),
  fetchGuard: vi.fn(async () => guardDetail),
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'orders' }),
}))
vi.mock('../lib/useSystemStatus', () => ({
  useSystemStatus: () => ({ data: undefined }),
}))

import { Route } from './guards'

// autoCodeSplitting rewrites `component` into a dynamic import of the route's
// split chunk, so the page itself is reached through that loader.
let GuardsPage: ComponentType

beforeAll(async () => {
  const split = await (
    Route as unknown as {
      component: () => Promise<{ component: ComponentType }>
    }
  ).component()
  GuardsPage = split.component
})

afterEach(() => {
  cleanup()
  guardsQuery.data = undefined
  guardsQuery.isLoading = false
  guardsQuery.isError = false
  guardsQuery.error = null
})

describe('guards list states', () => {
  it('renders the shared empty-state anatomy once the list lands empty', () => {
    guardsQuery.data = { guards: [] }
    render(<GuardsPage />)

    // The title used to be body text, so heading navigation skipped it. [F-17]
    expect(screen.getByRole('heading', { name: 'No guards yet' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Create a guard/ })).toBeTruthy()
  })

  it('skeletons the list container instead of a spinner and a sentence', () => {
    guardsQuery.isLoading = true
    render(<GuardsPage />)

    // /queries and /cache skeleton their rows; guards used Spinner + text. [F-08]
    expect(screen.getByText('Loading guards')).toBeTruthy()
    expect(screen.queryByText('Loading guards…')).toBeNull()
    expect(document.querySelectorAll('#skeleton').length).toBeGreaterThan(0)
  })

  it('shows a failed fetch the way the rest of the app shows one', () => {
    guardsQuery.isError = true
    guardsQuery.error = new Error('Upstream service unavailable')
    render(<GuardsPage />)

    // A bare red sentence with no retry and no detail. [F-07]
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByText("Guards couldn't be loaded")).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Technical details' })
    ).toBeTruthy()
    expect(screen.queryByText(/Failed to load guards/)).toBeNull()
  })

  it('names the guard field for its input and explains itself while creating', async () => {
    guardsQuery.data = { guards: [] }
    render(<GuardsPage />)

    fireEvent.click(screen.getByRole('button', { name: /Create a guard/ }))
    const name = await screen.findByLabelText('Name')
    expect(name.hasAttribute('disabled')).toBe(false)
    expect(
      screen.getByText('This is how you will refer to the guard everywhere.')
    ).toBeTruthy()
  })

  it('says why the name is locked when an existing guard is edited', async () => {
    guardsQuery.data = {
      guards: [
        {
          name: 'pii-mask',
          derived: false,
          description: '',
          mask_count: 0,
          max_rows: 1000,
          rules: [],
        },
      ],
    }
    render(<GuardsPage />)

    fireEvent.click(screen.getAllByText('pii-mask')[0])
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))

    // A disabled field with no explanation left a mistyped name with nowhere
    // to go but delete-and-recreate. [F-13]
    const locked = await screen.findByLabelText('Name')
    expect(locked.hasAttribute('disabled')).toBe(true)
    expect(
      screen.getByText(
        "A guard's name is its identity, so it can't be changed after it is created."
      )
    ).toBeTruthy()
  })

  it('withholds the count until the list has settled', () => {
    guardsQuery.isLoading = true
    const { rerender } = render(<GuardsPage />)
    // "Guards (0)" asserted an empty install while the request was in
    // flight, and kept asserting it after the request failed. [F-06]
    expect(screen.queryAllByText('Guards (0)').length).toBe(0)

    guardsQuery.isLoading = false
    guardsQuery.isError = true
    rerender(<GuardsPage />)
    expect(screen.queryAllByText('Guards (0)').length).toBe(0)

    guardsQuery.isError = false
    guardsQuery.data = { guards: [] }
    rerender(<GuardsPage />)
    expect(screen.getAllByText('Guards (0)').length).toBeGreaterThan(0)
  })
})
