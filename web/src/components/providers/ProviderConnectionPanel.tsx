import { Button } from '@rs/ui-new/button'
import { InlineNotice } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import type { ComponentType, ReactNode } from 'react'
import { useState } from 'react'
import type {
  FleetProviderLoginStart,
  FleetProviderLoginStatus,
  FleetProviderSlug,
} from '../../lib/useFleet'
import { COMPACT_CARD, shorten } from './cardShell'
import { useProviderOAuthLogin } from './useProviderOAuthLogin'

/** What every account provider reports about the credentials RDST holds. */
export interface ProviderStatus {
  connected: boolean
  method: string | null
  detail: string | null
}

export interface ProviderPanelClient<S extends ProviderStatus> {
  fetchStatus: () => Promise<S>
  logout: () => Promise<void>
  /** Both present exactly when the provider offers a browser sign-in. */
  startLogin?: () => Promise<FleetProviderLoginStart>
  pollLogin?: (loginId: string) => Promise<FleetProviderLoginStatus>
}

const noBrowserSignIn = () =>
  Promise.reject(new Error('This provider has no browser sign-in.'))

/**
 * One provider's connection state, rendered either as a settings card
 * (`compact`) or as the full panel at the top of the discovery drawer.
 *
 * Everything that differs between providers arrives as a prop: the panel owns
 * the states themselves - checking, connected, rejected credentials, signed
 * out - and the OAuth flow when the provider has one.
 */
export function ProviderConnectionPanel<S extends ProviderStatus>({
  slug,
  label,
  Logo,
  client,
  pitch,
  blurb,
  connectedLine,
  rejectedTitle,
  connectLabel = 'Sign in',
  credentialSlot,
  enabled = true,
  compact = false,
  onSignIn,
}: {
  slug: FleetProviderSlug
  label: string
  Logo: ComponentType<{ size?: number }>
  client: ProviderPanelClient<S>
  /** Compact signed-out line: what connecting this provider buys. */
  pitch: string
  /** Full-panel line under the "Connect X" heading. */
  blurb: string
  /** The connected line, which only the provider itself can phrase. */
  connectedLine: (status: S) => string
  /** Rejected-credential heading, without its trailing period. */
  rejectedTitle: string
  /** The compact card's signed-out action. */
  connectLabel?: string
  /** A pasted-credential path, rendered below or in place of the sign-in. */
  credentialSlot?: (context: { onSaved: () => Promise<unknown> }) => ReactNode
  enabled?: boolean
  /** Settings-page rendering: one quiet card per connection state. */
  compact?: boolean
  /** Where the compact signed-out card sends the user to connect. */
  onSignIn?: () => void
}) {
  const [signingOut, setSigningOut] = useState(false)

  const {
    data: status,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: [`fleet-${slug}-status`],
    queryFn: client.fetchStatus,
    enabled,
    // Provider sessions change only when the user acts on them, and every
    // action here refetches explicitly.
    staleTime: 300_000,
    refetchOnWindowFocus: false,
    retry: false,
  })

  const oauth = Boolean(client.startLogin && client.pollLogin)
  const { loginId, loginStatus, loginError, starting, beginLogin, clearError } =
    useProviderOAuthLogin({
      label,
      start: client.startLogin ?? noBrowserSignIn,
      poll: client.pollLogin ?? noBrowserSignIn,
      onSuccess: refetch,
    })

  const testId = `${slug}-connection-panel`

  if (!enabled) return null
  if (isLoading) {
    const checking = (
      <HStack className="gap-2 items-center">
        <Spinner size="base" />
        <Text level="caption" className="text-content-layout-3">
          {`Checking the ${label} connection...`}
        </Text>
      </HStack>
    )
    return compact ? (
      <div className={COMPACT_CARD} data-testid={testId}>
        {checking}
      </div>
    ) : (
      <div data-testid={testId}>{checking}</div>
    )
  }
  // A provider the user may never touch stays silent about its own failures
  // when it is only a settings card.
  if (isError)
    return compact ? null : (
      <InlineNotice
        errorClass="rdst-service"
        title={`Couldn't check the ${label} connection`}
        message={error instanceof Error ? error.message : String(error)}
        trustworthy="Your fleet inventory and saved reports are unaffected."
        onRetry={() => void refetch()}
        retryLabel="Re-check"
      />
    )
  if (!status) return null

  if (status.connected) {
    const signOut = () => {
      setSigningOut(true)
      void client
        .logout()
        .catch(() => {})
        .then(() => refetch())
        .finally(() => setSigningOut(false))
    }
    const line = connectedLine(status)
    if (compact)
      return (
        <div className={COMPACT_CARD} data-testid={testId}>
          <HStack className="gap-2 items-center">
            <Logo size={20} />
            <Text level="label-small" className="text-content-layout-1">
              {label}
            </Text>
          </HStack>
          <HStack className="gap-1.5 items-start">
            <Icon
              name="tick-double"
              label="Connected"
              className="w-4 h-4 text-content-positive-soft shrink-0"
            />
            <Text level="caption" className="text-content-positive-soft">
              {line}
            </Text>
          </HStack>
          <Button
            variant="primary"
            modifier="outline"
            fullWidth
            className="mt-auto"
            icon="logout"
            iconPosition="left"
            label={signingOut ? 'Signing out...' : 'Sign out'}
            loading={signingOut}
            onClick={signOut}
          />
        </div>
      )
    return (
      <div data-testid={testId}>
        <HStack className="gap-2 items-center flex-wrap">
          <Logo size={16} />
          <Icon
            name="tick-double"
            label="Connected"
            className="w-4 h-4 text-content-positive-soft shrink-0"
          />
          <Text level="caption" className="text-content-positive-soft">
            {line}
          </Text>
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            icon="logout"
            iconPosition="left"
            label={signingOut ? 'Signing out...' : 'Sign out'}
            loading={signingOut}
            onClick={signOut}
          />
        </HStack>
      </div>
    )
  }

  // Credentials the provider refused (expired, revoked, missing scopes). It is
  // not the same as never having connected, and saying nothing leaves the user
  // staring at an unexplained sign-in prompt.
  const rejectedDetail = status.method
    ? status.detail || `${rejectedTitle}.`
    : null
  const failureHeading = `${label} ${oauth ? 'sign-in' : 'connection'} failed`
  const signingIn = starting || Boolean(loginId)
  const signInLabel = loginId
    ? 'Waiting for browser approval'
    : starting
      ? 'Opening browser…'
      : `Sign in with ${label}`

  // Signed out and only a settings card: name the provider, say what connecting
  // buys, and give one obvious way in. A rejection replaces the pitch with a
  // warning line, because it is a failure the user hit.
  if (compact)
    return (
      <div className={COMPACT_CARD} data-testid={testId}>
        <HStack className="gap-2 items-center">
          <Logo size={20} />
          <Text level="label-small" className="text-content-layout-1">
            {label}
          </Text>
        </HStack>
        {rejectedDetail ? (
          <VStack className="gap-1 items-stretch">
            <HStack className="gap-1.5 items-center">
              <Icon
                name="alert"
                label={oauth ? 'Sign-in problem' : 'Connection problem'}
                className="w-4 h-4 text-content-warning-soft shrink-0"
              />
              <Text level="caption" className="text-content-warning-soft">
                {failureHeading}
              </Text>
            </HStack>
            <Text level="caption" className="text-content-layout-3">
              {shorten(rejectedDetail, 90)}
            </Text>
          </VStack>
        ) : (
          <Text level="caption" className="text-content-layout-3">
            {pitch}
          </Text>
        )}
        <Button
          variant="primary"
          modifier="outline"
          fullWidth
          className="mt-auto"
          label={loginId ? 'Waiting for approval' : connectLabel}
          loading={Boolean(loginId)}
          onClick={() => {
            if (onSignIn) onSignIn()
            else if (oauth) void beginLogin()
          }}
        />
      </div>
    )

  return (
    <VStack
      className="gap-4 items-stretch rounded-xl border border-border-layout-1 bg-surface-layout-1 p-4"
      data-testid={testId}
    >
      {rejectedDetail && !loginId && (
        <InlineNotice
          errorClass="provider"
          icon="alert"
          title={rejectedTitle}
          message={shorten(rejectedDetail, 220)}
          trustworthy={`Targets you already imported from ${label} are unaffected.`}
        />
      )}

      <VStack className="gap-3 items-stretch">
        <VStack className="gap-1 items-start">
          <HStack className="gap-2 items-center">
            <Logo />
            <Text level="label-small" className="text-content-layout-1">
              {`Connect ${label}`}
            </Text>
          </HStack>
          <Text level="body-small" className="text-content-layout-2">
            {blurb}
          </Text>
        </VStack>
        {/* A provider with a browser sign-in leads with it, and keeps any
            pasted credential as the fallback below. One without a sign-in has
            nothing else to lead with: the credential is the whole panel. */}
        {oauth ? (
          <VStack className="gap-1.5 items-start">
            <Button
              variant="primary"
              modifier="solid"
              label={signInLabel}
              loading={signingIn}
              disabled={signingIn}
              onClick={() => void beginLogin()}
            />
            {loginId && (
              <Text level="caption" className="text-content-layout-3">
                {loginStatus?.detail ||
                  'Authorize RDST in the browser tab we opened.'}
              </Text>
            )}
          </VStack>
        ) : (
          credentialSlot?.({ onSaved: refetch })
        )}
      </VStack>

      {loginError && (
        <div className="rounded-lg border border-border-negative-soft bg-surface-negative-soft/20 p-3">
          <VStack className="gap-2 items-stretch">
            <Text level="label-small" className="text-content-negative-soft">
              {failureHeading}
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              {loginError.message}
            </Text>
            <HStack>
              <Button
                variant="primary"
                modifier="outline"
                size="small"
                label="Try again"
                onClick={() => {
                  clearError()
                  void beginLogin()
                }}
              />
            </HStack>
          </VStack>
        </div>
      )}

      {oauth && credentialSlot?.({ onSaved: refetch })}
    </VStack>
  )
}
