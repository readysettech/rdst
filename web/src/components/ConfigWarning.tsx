import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { type EnvRequirement, fetchAccountStatus } from '../lib/api'
import { TRIAL_EXHAUSTED_MESSAGE } from '../lib/errorContract'
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../lib/trialQueries'
import { useSystemStatus } from '../lib/useSystemStatus'
import { EnvSecretsDialog } from './EnvSecretsDialog'
import { TrialRegistrationDialog } from './TrialRegistrationDialog'

interface WarningConfig {
  title: string
  description: string
  severity: 'error' | 'warning'
  actionLabel?: string
  actionType?: 'open-env-dialog' | 'open-trial-dialog'
  secondaryActionLabel?: string
  secondaryActionType?: 'open-trial-dialog' | 'open-env-dialog'
}

interface TrialState {
  active: boolean
  percent_remaining?: number
  remaining_tokens_display?: string
  limit_tokens_display?: string
}

function getWarningConfig(
  missingAnthropicRequirements: EnvRequirement[],
  trialState?: TrialState
): WarningConfig | null {
  // Trial exhausted — needs a real key
  if (
    trialState?.active &&
    trialState.percent_remaining != null &&
    trialState.percent_remaining <= 0
  ) {
    return {
      title: 'Included AI used up',
      description: TRIAL_EXHAUSTED_MESSAGE,
      severity: 'error',
      actionLabel: 'Set API key',
      actionType: 'open-env-dialog',
    }
  }

  // Trial low balance
  if (
    trialState?.active &&
    trialState.percent_remaining != null &&
    trialState.percent_remaining < 25
  ) {
    return {
      title: 'Included AI running low',
      description: `${trialState.remaining_tokens_display} of ${trialState.limit_tokens_display} included AI tokens remaining. Consider adding your own API key.`,
      severity: 'warning',
      actionLabel: 'Set API key',
      actionType: 'open-env-dialog',
    }
  }

  // Missing credentials: offer Readyset sign-in or Claude BYOK.
  if (missingAnthropicRequirements.length > 0) {
    return {
      title: 'AI sign-in required',
      description:
        'Sign in to Readyset for the included AI, or add your own Anthropic API key.',
      severity: 'warning',
      actionLabel: 'Sign in to Readyset',
      actionType: 'open-trial-dialog',
      secondaryActionLabel: 'Set API key',
      secondaryActionType: 'open-env-dialog',
    }
  }

  return null
}

/** One inline line: the sentence in its tone, then the actions as ghost buttons. */
function ConfigBanner({
  config,
  onDismiss,
  onAction,
  onSecondaryAction,
}: {
  config: WarningConfig
  onDismiss?: () => void
  onAction?: () => void
  onSecondaryAction?: () => void
}) {
  const isError = config.severity === 'error'
  return (
    <div
      data-testid="config-notice"
      className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1"
    >
      <Text
        as="p"
        level="body-small"
        className={
          isError ? 'text-content-negative-soft' : 'text-content-warning-soft'
        }
      >
        {config.description}
      </Text>
      {config.actionLabel && onAction && (
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label={config.actionLabel}
          onClick={onAction}
        />
      )}
      {config.secondaryActionLabel && onSecondaryAction && (
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label={config.secondaryActionLabel}
          onClick={onSecondaryAction}
        />
      )}
      {!isError && onDismiss && (
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Hide"
          onClick={onDismiss}
        />
      )}
    </div>
  )
}

/** The shell's configuration notice. */
export function ConfigWarning() {
  return <AiAccessNotice />
}

function AiAccessNotice() {
  const queryClient = useQueryClient()
  const location = useLocation()
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [showSecretsDialog, setShowSecretsDialog] = useState(false)
  const [showTrialDialog, setShowTrialDialog] = useState(false)

  const { data: status, isLoading, error } = useSystemStatus()
  const { envRequirements, isTrialSource, trialStatus } = useTrialSource()
  const account = useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
  })

  // A Readyset sign-in provides the included AI: a stale requirement never asks for one.
  const missingAnthropicRequirements = account.data?.signed_in
    ? []
    : envRequirements?.requirements.filter(
        (item) => !item.satisfied && item.kind === 'anthropic_api_key'
      ) || []

  const trialState: TrialState | undefined =
    isTrialSource &&
    (trialStatus?.active || trialStatus?.status === 'exhausted')
      ? {
          active: true,
          percent_remaining:
            trialStatus.status === 'exhausted'
              ? 0
              : (trialStatus.percent_remaining ?? undefined),
          remaining_tokens_display:
            trialStatus.remaining_tokens_display ?? undefined,
          limit_tokens_display: trialStatus.limit_tokens_display ?? undefined,
        }
      : undefined

  // Reset dismissed state when status changes
  useEffect(() => {
    const config = getWarningConfig(missingAnthropicRequirements, trialState)
    if (config?.title !== dismissed) {
      setDismissed(null)
    }
  }, [missingAnthropicRequirements, trialState, dismissed])

  if (isLoading || account.isPending) {
    return null
  }

  if (error) return null

  if (!status) return null

  const warningConfig = getWarningConfig(
    missingAnthropicRequirements,
    trialState
  )

  if (!warningConfig || dismissed === warningConfig.title) {
    return null
  }

  // The banner only belongs on pages that use the key (Ask, Audit, ...).
  // The home page stays welcome-clean (the Ask card carries its own "needs a
  // key" chip), and the demo runs entirely without an Anthropic key.
  // The audit page runs its own AI-key gate (preflight row + trial sign-up),
  // and the monitoring pages carry sign-in on the database's monitoring state,
  // so the global banner would say the same thing twice there.
  if (
    location.pathname === '/' ||
    location.pathname.startsWith('/demo') ||
    location.pathname.startsWith('/audit') ||
    location.pathname.startsWith('/db') ||
    location.pathname.startsWith('/database') ||
    location.pathname.startsWith('/inbox') ||
    location.pathname.startsWith('/agent-activity')
  ) {
    return null
  }

  const shouldShowManualAnthropicInput =
    warningConfig.actionType === 'open-env-dialog' ||
    warningConfig.secondaryActionType === 'open-env-dialog'

  const handleAction = () => {
    if (warningConfig.actionType === 'open-env-dialog') {
      setShowSecretsDialog(true)
    } else if (warningConfig.actionType === 'open-trial-dialog') {
      setShowTrialDialog(true)
    }
  }

  const handleSecondaryAction = () => {
    if (warningConfig.secondaryActionType === 'open-env-dialog') {
      setShowSecretsDialog(true)
    } else if (warningConfig.secondaryActionType === 'open-trial-dialog') {
      setShowTrialDialog(true)
    }
  }

  const invalidateAll = () => {
    void invalidateTrialRelatedQueries(queryClient)
  }

  return (
    <>
      <ConfigBanner
        config={warningConfig}
        onDismiss={() => setDismissed(warningConfig.title)}
        onAction={warningConfig.actionType ? handleAction : undefined}
        onSecondaryAction={
          warningConfig.secondaryActionType ? handleSecondaryAction : undefined
        }
      />
      <EnvSecretsDialog
        isOpen={showSecretsDialog}
        onClose={() => setShowSecretsDialog(false)}
        requirements={missingAnthropicRequirements}
        showManualAnthropicInput={shouldShowManualAnthropicInput}
        keyringAvailable={envRequirements?.keyring_available ?? false}
        onSuccess={invalidateAll}
        onTrialRegister={() => setShowTrialDialog(true)}
      />
      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => setShowTrialDialog(false)}
        onSuccess={() => {
          invalidateAll()
          setShowTrialDialog(false)
        }}
      />
    </>
  )
}
