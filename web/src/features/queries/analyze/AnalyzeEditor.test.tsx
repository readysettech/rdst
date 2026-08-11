import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AnalyzeController } from './useAnalyzeController'

vi.mock('../../../components/TargetLockNotice', () => ({
  TargetLockNotice: ({ message }: { message: string }) => (
    <span>{message}</span>
  ),
}))

vi.mock('./AnalyzeQueryEditor', () => ({
  AnalyzeQueryEditor: ({
    disabled,
    target,
  }: {
    disabled?: boolean
    target?: string | null
  }) => (
    <span
      data-testid="query-editor"
      data-disabled={disabled}
      data-target={target}
    >
      editor
    </span>
  ),
}))

import { AnalyzeEditor } from './AnalyzeEditor'

afterEach(cleanup)

function controller(isLocked: boolean): AnalyzeController {
  return {
    editor: {
      value: 'SELECT 1',
      setValue: vi.fn(),
      fast: false,
      setFast: vi.fn(),
      ref: { current: null },
      isHighlighted: false,
      disabled: isLocked,
    },
    target: {
      name: 'prod',
      lock: {
        isResolved: true,
        isLocked,
        targetName: 'prod',
        message: 'Add the password for prod.',
        missingTargetRequirements: [],
        keyringAvailable: true,
      },
    },
    history: {
      queries: [],
      isLoading: false,
      error: null,
      retry: vi.fn(),
    },
    actions: {
      analyze: vi.fn(),
      selectHistory: vi.fn(),
    },
  }
}

describe('AnalyzeEditor', () => {
  it('shows the target recovery notice and disables the editor when locked', () => {
    render(<AnalyzeEditor controller={controller(true)} />)

    expect(screen.getByText('Add the password for prod.')).toBeTruthy()
    expect(screen.getByTestId('query-editor').dataset.disabled).toBe('true')
    expect(screen.getByTestId('query-editor').dataset.target).toBe('prod')
  })

  it('keeps the unlocked editor ready without a recovery notice', () => {
    render(<AnalyzeEditor controller={controller(false)} />)

    expect(screen.queryByText('Add the password for prod.')).toBeNull()
    expect(screen.getByTestId('query-editor').dataset.disabled).toBe('false')
  })
})
