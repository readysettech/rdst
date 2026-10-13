import { InlineNotice } from '@rs/ui-new/error-state'
import { useNavigate } from '@tanstack/react-router'
import { useAiGate } from '../lib/useAiGate'

/**
 * The AI dependency asked for by the feature that needs it, at the moment it is
 * needed — not by a chooser in front of the whole app. Everything around it
 * keeps working, so a user with no key can still connect databases, run health
 * checks and read saved reports. Renders nothing while the probe is in flight
 * or once a provider is connected. [USE-006, USE-050, USE-068]
 */
export function AiSetupNotice() {
  const gate = useAiGate()
  const navigate = useNavigate()
  if (gate.status !== 'blocked') return null

  return (
    <InlineNotice
      tone="quiet"
      errorClass="provider"
      accent="warning"
      title=""
      message="AI checks paused. Sign in or add a key in Settings."
      action={{
        label: 'Set up AI',
        onClick: () =>
          void navigate({ to: '/configure', search: { panel: 'ai' } }),
      }}
    />
  )
}

/** `true` when an AI-dependent action must not be offered as available. */
export function useAiBlocked(): boolean {
  return useAiGate().status === 'blocked'
}
