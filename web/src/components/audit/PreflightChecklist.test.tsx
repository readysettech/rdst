import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import type { AuditPreflightResult } from '../../lib/auditPreflight'
import type { EnvRequirement } from '../../lib/api'
import { TRIAL_EXHAUSTED_MESSAGE } from '../../lib/errorContract'
import { PreflightChecklist, type AiPreflightGate } from './PreflightChecklist'

vi.mock('../aws/AwsConnectionPanel', () => ({
  AwsConnectionPanel: () => <div data-testid="aws-connection-panel" />,
}))
vi.mock('../TrialRegistrationDialog', () => ({
  TrialRegistrationDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="trial-dialog" /> : null,
}))
vi.mock('../EnvSecretsDialog', () => ({
  EnvSecretsDialog: ({
    isOpen,
    showManualAnthropicInput,
  }: {
    isOpen: boolean
    showManualAnthropicInput?: boolean
  }) =>
    isOpen ? (
      <div
        data-testid={
          showManualAnthropicInput ? 'anthropic-key-dialog' : 'password-dialog'
        }
      />
    ) : null,
}))

afterEach(cleanup)

const passwordRequirement: EnvRequirement = {
  kind: 'target_password',
  target: 'orders',
  satisfied: false,
  source: 'missing',
  accepted_names: ['RDST_ORDERS_PASSWORD'],
}

const anthropicRequirementFixture: EnvRequirement = {
  kind: 'anthropic_api_key',
  target: null,
  satisfied: false,
  source: 'missing',
  accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
}

function result(
  overrides: Partial<AuditPreflightResult> = {}
): AuditPreflightResult {
  return {
    requirements: {
      orders: {
        target: 'orders',
        engine: 'postgresql',
        query_stats: 'ok',
        detail: 'ready',
        docker_available: true,
      },
    },
    errors: {},
    aws: { required: false },
    ...overrides,
  }
}

interface ChecklistOptions {
  preflight?: AuditPreflightResult
  liveCapture?: boolean
  aiGate?: AiPreflightGate
  passwordRequirements?: EnvRequirement[]
  anthropicRequirement?: EnvRequirement
}

function checklist(options: ChecklistOptions = {}) {
  const {
    preflight = result(),
    liveCapture = true,
    aiGate = { status: 'ready' } as AiPreflightGate,
    passwordRequirements = [],
    anthropicRequirement,
  } = options
  return (
    <PreflightChecklist
      result={preflight}
      busy={false}
      onRecheck={vi.fn()}
      liveCapture={liveCapture}
      aiGate={aiGate}
      passwordRequirements={passwordRequirements}
      anthropicRequirement={anthropicRequirement}
      keyringAvailable
    />
  )
}

/**
 * Render the checklist and return a `rerenderWith` bound to the same options,
 * so a test only re-states what its next frame changes.
 */
function renderChecklist(options: ChecklistOptions = {}) {
  const view = renderWithClient(checklist(options))
  return {
    ...view,
    rerenderWith: (next: ChecklistOptions) =>
      view.rerender(checklist({ ...options, ...next })),
  }
}

describe('PreflightChecklist', () => {
  it('offers an inline password action without exposing shell instructions', () => {
    renderChecklist({
      preflight: result({
        requirements: {},
        errors: {
          orders: {
            code: 'TARGET_PASSWORD_REQUIRED',
            message:
              'export RDST_ORDERS_PASSWORD or set the environment variable',
            target: 'orders',
            passwordEnv: 'RDST_ORDERS_PASSWORD',
          },
        },
      }),
      passwordRequirements: [passwordRequirement],
    })

    expect(
      screen.getByText("Enter the password for 'orders' again.")
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Set password/ }))
    expect(screen.getByTestId('password-dialog')).toBeTruthy()
    const visibleCopy = document.body.textContent?.toLowerCase() ?? ''
    expect(visibleCopy).not.toContain('export ')
    expect(visibleCopy).not.toContain('environment variable')
  })

  it('shows Docker as a live-capture gate but omits it for metrics-only runs', () => {
    const unavailable = result({
      requirements: {
        orders: {
          target: 'orders',
          engine: 'postgresql',
          query_stats: 'ok',
          detail: 'ready',
          docker_available: false,
        },
      },
    })
    const view = renderChecklist({ preflight: unavailable, liveCapture: true })
    expect(
      screen.getByText(/The run stays blocked until Docker is available/)
    ).toBeTruthy()

    view.rerenderWith({ liveCapture: false })
    expect(screen.queryByText('Docker', { exact: true })).toBeNull()
    expect(screen.queryByText(/run stays blocked/i)).toBeNull()
  })

  it('names both AWS account-mismatch recovery paths', () => {
    renderChecklist({
      preflight: result({
        aws: {
          required: true,
          status: {
            has_credentials: true,
            method: 'sso',
            identity_arn: 'arn:aws:sts::111:assumed-role/dev/user',
            account: '111',
            active_profile: 'dev',
            available_profiles: ['dev', 'prod'],
            region: 'us-east-1',
          },
          mismatchedTargets: ['orders'],
          mismatchedAccount: '222',
        },
      }),
    })

    expect(
      screen.getByText(/sign in with a profile for account 222/i)
    ).toBeTruthy()
    expect(
      screen.getByText(/select only databases from account 111/i)
    ).toBeTruthy()
    expect(screen.getByTestId('aws-connection-panel')).toBeTruthy()
  })

  it('reflects the AI gate and offers both exhausted-trial recovery actions', () => {
    const view = renderChecklist({ aiGate: { status: 'ready' } })
    expect(screen.getByText('Valid key or trial is ready')).toBeTruthy()

    view.rerenderWith({ aiGate: { status: 'blocked', reason: 'exhausted' } })
    expect(screen.getByText(TRIAL_EXHAUSTED_MESSAGE)).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', { name: 'Start free trial' })
    )
    expect(screen.getByTestId('trial-dialog')).toBeTruthy()
  })

  it('sets the key in place instead of routing to Settings', () => {
    renderChecklist({
      aiGate: { status: 'blocked', reason: 'missing' },
      anthropicRequirement: anthropicRequirementFixture,
    })

    expect(screen.queryByRole('link', { name: /Set key/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Set key/ }))
    expect(screen.getByTestId('anthropic-key-dialog')).toBeTruthy()
  })
})
