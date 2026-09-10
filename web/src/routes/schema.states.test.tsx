import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock({ useNavigate: () => vi.fn() })
})

const schemaState = {
  status: null as unknown,
  schema: null as unknown,
  error: null as string | null,
  errorEnvelope: null as unknown,
  loading: false,
  checkStatus: vi.fn(async () => null),
  loadSchema: vi.fn(async () => null),
  initSchema: vi.fn(),
  exportSchema: vi.fn(),
  deleteSchema: vi.fn(),
  addColumn: vi.fn(),
  addTable: vi.fn(),
  addTerminology: vi.fn(),
  addEnum: vi.fn(),
  addRelationship: vi.fn(),
  addMetric: vi.fn(),
  refreshSchema: vi.fn(),
  profileSchema: vi.fn(),
  clearError: vi.fn(),
}

vi.mock('../lib/useSchema', () => ({ useSchema: () => schemaState }))
vi.mock('../hooks/useTarget', () => ({
  useTargetResolution: () => ({
    target: 'orders',
    setTarget: vi.fn(),
    isResolving: false,
    isUnavailable: false,
    refetch: vi.fn(),
  }),
  useTarget: () => ({ target: 'orders' }),
}))
vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: () => ({
    isLocked: false,
    isResolved: true,
    message: null,
    missingTargetRequirements: [],
    keyringAvailable: true,
  }),
}))
vi.mock('../lib/backgroundRuns', () => ({ useBackgroundRun: () => undefined }))

import { Route } from './schema'

let SchemaPage: ComponentType

beforeAll(async () => {
  const split = await (
    Route as unknown as {
      component: () => Promise<{ component: ComponentType }>
    }
  ).component()
  SchemaPage = split.component
})

afterEach(() => {
  cleanup()
  schemaState.loading = false
  schemaState.error = null
})

describe('schema loading', () => {
  it('skeletons the table list instead of pulsing an icon', () => {
    schemaState.loading = true
    render(<SchemaPage />)

    // Was a 56px throbbing square and "Loading schema...". [C-71]
    expect(screen.getByText('Loading semantic layer')).toBeTruthy()
    expect(screen.queryByText('Loading schema...')).toBeNull()
    expect(document.querySelectorAll('#skeleton').length).toBeGreaterThan(0)
  })

  it('explains the semantic layer through the design system info control', () => {
    // Was a hand-drawn "i" in a 24px circle, below the WCAG target size. [C-74]
    render(<SchemaPage />)

    const info = screen.getAllByRole('button', {
      name: 'What is the semantic layer?',
    })[0]
    expect(info.textContent).not.toBe('i')
    expect(info.querySelector('svg')).toBeTruthy()
  })
})
