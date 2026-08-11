import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { isSshErrorCategory, sshErrorCopy } from '../lib/sshErrors'
import { testTunnel } from '../lib/tunnels'
import { ProviderAllowlistPanel } from './ProviderAllowlistPanel'

export interface CategorizedConnectionFailure {
  target: string
  message?: string | null
  category?: string | null
  code?: string | null
}

export function ConnectionFailureActions({
  failure,
  passwordRequired = false,
  onSetPassword,
  onRetry,
  onResolved,
  featureRecovery = false,
}: {
  failure: CategorizedConnectionFailure
  passwordRequired?: boolean
  onSetPassword?: () => void
  onRetry?: () => Promise<boolean>
  onResolved?: () => void
  /** Feature pages opt in to settings/tunnel recovery; connection forms own
   *  those controls already and leave this false. */
  featureRecovery?: boolean
}) {
  const navigate = useNavigate()
  const [testingTunnel, setTestingTunnel] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const category = failure.category ?? failure.code
  const message = failure.message ?? 'The connection test failed.'
  const providerBlocked = category?.startsWith('provider_ip_blocked') ?? false
  // Provider IP checks happen before database authentication. A stored or
  // missing password says nothing useful until the address is allowlisted.
  const effectivePasswordRequired = passwordRequired && !providerBlocked
  const displayMessage = effectivePasswordRequired
    ? `Enter the password for '${failure.target}' again.`
    : isSshErrorCategory(category)
      ? sshErrorCopy({ category, message, target: failure.target })
      : message.split('\n')[0]
  const sshFailure = isSshErrorCategory(category)

  const openSettings = () => {
    const returnTo = `${window.location.pathname}${window.location.search}`
    navigate({
      to: '/configure',
      search: { edit: failure.target, returnTo },
    })
  }

  const handleTestTunnel = async () => {
    setTestingTunnel(true)
    setActionError(null)
    try {
      const result = await testTunnel(failure.target)
      if (!result.ok) {
        setActionError(result.message || 'The tunnel test failed.')
        return
      }
      const recovered = onRetry ? await onRetry() : true
      if (recovered) onResolved?.()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error))
    } finally {
      setTestingTunnel(false)
    }
  }

  return (
    <VStack className="gap-2 items-stretch">
      {effectivePasswordRequired && onSetPassword ? (
        <div className="rounded-xl border border-border-layout-1 bg-surface-layout-1 p-3 shadow-small">
          <HStack className="items-center gap-3 flex-wrap">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-warning-soft">
              <Icon
                name="key"
                label=""
                aria-hidden="true"
                className="size-4 text-content-warning-soft"
              />
            </div>
            <Text
              level="body-small"
              className="min-w-48 flex-1 text-content-layout-1"
            >
              {displayMessage}
            </Text>
            <Button
              variant="rising"
              modifier="solid"
              size="base"
              icon="key"
              iconPosition="left"
              label="Set password"
              className="shrink-0"
              onClick={onSetPassword}
            />
          </HStack>
        </div>
      ) : (
        <Text level="caption" className="text-content-negative-soft">
          {displayMessage}
        </Text>
      )}
      {providerBlocked && (
        <ProviderAllowlistPanel
          target={failure.target}
          onTestAgain={onRetry}
          onAdded={onResolved}
        />
      )}
      {featureRecovery && (
        <HStack className="gap-2 items-center flex-wrap">
          {sshFailure && (
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              icon="connect"
              iconPosition="left"
              label="Test tunnel"
              loading={testingTunnel}
              onClick={() => void handleTestTunnel()}
            />
          )}
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon="database-settings"
            iconPosition="left"
            label="Open connection settings"
            onClick={openSettings}
          />
        </HStack>
      )}
      {actionError && (
        <Text level="caption" className="text-content-negative-soft">
          {actionError.split('\n')[0]}
        </Text>
      )}
    </VStack>
  )
}
