import { InlineNotice } from '@rs/ui-new/error-state'
import { useNavigate } from '@tanstack/react-router'
import { type AiGate, useAiGate } from '../lib/useAiGate'

function reasonMessage(
  gate: Extract<AiGate, { status: 'blocked' }>,
  feature: string
): string {
  if (gate.reason === 'exhausted') {
    return `Your included AI is used up, so ${feature} cannot run. Sign in to Readyset again or add an Anthropic key.`
  }
  if (gate.reason === 'invalid') {
    return `Anthropic rejected the saved key, so ${feature} cannot run. Update the key or sign in to Readyset.`
  }
  return `${feature} sends your question to an AI provider, so it needs one connected. Sign in to Readyset for the free included AI, or add your own Anthropic key.`
}

/**
 * The AI dependency asked for by the feature that needs it, at the moment it is
 * needed — not by a chooser in front of the whole app. Everything around it
 * keeps working, so a user with no key can still connect databases, run health
 * checks and read saved reports. Renders nothing while the probe is in flight
 * or once a provider is connected. [USE-006, USE-050, USE-068]
 */
export function AiSetupNotice({ feature }: { feature: string }) {
  const gate = useAiGate()
  const navigate = useNavigate()
  if (gate.status !== 'blocked') return null

  return (
    <InlineNotice
      errorClass="provider"
      accent="warning"
      icon="sparkles"
      title="Connect an AI provider to use this"
      message={reasonMessage(gate, feature)}
      trustworthy="Everything else in RDST works without it."
      action={{
        label: 'Set up AI',
        icon: 'key',
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
