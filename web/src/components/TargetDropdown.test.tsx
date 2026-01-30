import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TargetDropdown } from './TargetDropdown'
import { fetchStatus } from '../lib/api'
import {
  __resetTargetSwitchLockForTests,
  useTargetSwitchLock,
} from '../lib/targetSwitchLock'

vi.mock('../lib/api', () => ({
  fetchStatus: vi.fn(),
}))

function LockHarness({ active }: { active: boolean }) {
  useTargetSwitchLock('ask', active)
  return null
}

function renderDropdown({
  selectedTarget = 'prod',
  lockActive = false,
  onSelectTarget = vi.fn(),
}: {
  selectedTarget?: string
  lockActive?: boolean
  onSelectTarget?: (target: string | null) => void
}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <LockHarness active={lockActive} />
      <TargetDropdown selectedTarget={selectedTarget} onSelectTarget={onSelectTarget} />
    </QueryClientProvider>
  )

  return {
    ...rendered,
    onSelectTarget,
  }
}

describe('TargetDropdown lock behavior', () => {
  beforeEach(() => {
    __resetTargetSwitchLockForTests()
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: true,
      default_target: 'prod',
      targets: [
        { name: 'prod', has_password: true, is_default: true },
        { name: 'staging', has_password: true, is_default: false },
      ],
      version: '1.0.0',
      error: null,
    })
  })

  afterEach(() => {
    cleanup()
    __resetTargetSwitchLockForTests()
    vi.clearAllMocks()
  })

  it('renders disabled state and blocks target switching when lock is active', async () => {
    const { container, onSelectTarget } = renderDropdown({ lockActive: true })

    await screen.findByText('prod')

    const trigger = container.querySelector('[aria-disabled="true"]') as HTMLElement | null
    expect(trigger).not.toBeNull()

    fireEvent.click(trigger as HTMLElement)

    await waitFor(() => {
      expect(screen.queryByText('staging')).toBeNull()
    })
    expect(onSelectTarget).not.toHaveBeenCalled()
  })

  it('shows lock tooltip message when hovering locked trigger', async () => {
    const { container } = renderDropdown({ lockActive: true })

    await screen.findByText('prod')

    const trigger = container.querySelector('[aria-disabled="true"]') as HTMLElement | null
    expect(trigger).not.toBeNull()
    expect(trigger?.getAttribute('title')).toBe(
      'Target switching is disabled while Ask is in progress.'
    )
  })

  it('reconciles invalid selected target to the default target', async () => {
    const onSelectTarget = vi.fn()
    renderDropdown({ selectedTarget: 'deleted-db', onSelectTarget })

    await screen.findByText('prod')

    await waitFor(() => {
      expect(onSelectTarget).toHaveBeenCalledWith('prod')
    })
  })

  it('clears selection when selected target is invalid and no targets remain', async () => {
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: false,
      default_target: null,
      targets: [],
      version: '1.0.0',
      error: null,
    })

    const onSelectTarget = vi.fn()
    renderDropdown({ selectedTarget: 'deleted-db', onSelectTarget })

    expect(await screen.findByText('No Targets')).toBeTruthy()
    await waitFor(() => {
      expect(onSelectTarget).toHaveBeenCalledWith(null)
    })
  })
})
