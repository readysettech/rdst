import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { ErrorState } from '@rs/ui-new/error-state'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useMemo, useState } from 'react'
import { type EnvRequirement, setAiProvider } from '../lib/api'
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../lib/trialQueries'
import type { AiGate } from '../lib/useAiGate'
import { EnvSecretsDialog } from './EnvSecretsDialog'
import { AccountLoginDialog } from './TrialRegistrationDialog'

interface AiProviderGateProps {
  notice?: ReactNode
  gate: Extract<AiGate, { status: 'checking' | 'error' | 'blocked' }>
  /** The bar title of the page the gate stands in for. */
  title?: string
}

/** The page's own bar, then the gate on the content axis. */
function GateFrame({
  title,
  notice,
  children,
}: {
  title: string
  notice?: ReactNode
  children: ReactNode
}) {
  return (
    <>
      <PageBar title={title} />
      <PageContainer className="pt-9 pb-24">
        {notice}
        {children}
      </PageContainer>
    </>
  )
}

export function AiProviderGate({
  gate,
  notice,
  title = 'Home',
}: AiProviderGateProps) {
  const queryClient = useQueryClient()
  const { envRequirementsQuery, anthropicRequirement } = useTrialSource()
  const [accountOpen, setAccountOpen] = useState(false)
  const [keyOpen, setKeyOpen] = useState(false)
  const accountConnected = Boolean(
    anthropicRequirement?.readyset_account_connected
  )
  const useAccount = useMutation({
    mutationFn: () => setAiProvider('readyset'),
    onSuccess: () => void invalidateTrialRelatedQueries(queryClient),
  })
  // A connected account needs no new sign-in, only switching AI to it.
  const signIn = () =>
    accountConnected ? useAccount.mutate() : setAccountOpen(true)

  if (gate.status === 'checking') {
    return (
      <GateFrame title={title} notice={notice}>
        <HStack className="items-center gap-2.5">
          <Spinner size="base" color="layout" />
          <Text level="body-small" className="text-content-layout-3">
            Checking your AI setup
          </Text>
        </HStack>
      </GateFrame>
    )
  }

  if (gate.status === 'error') {
    return (
      <GateFrame title={title} notice={notice}>
        <ErrorState
          tone="quiet"
          errorClass="rdst-service"
          title="Could not check your AI setup"
          message={gate.message}
          onRetry={() => void envRequirementsQuery.refetch()}
        />
      </GateFrame>
    )
  }

  const gateError =
    useAccount.error instanceof Error
      ? useAccount.error.message
      : gate.reason === 'invalid'
        ? accountConnected
          ? 'Anthropic rejected the saved key. Add a valid key or use Hosted by Readyset.'
          : 'Anthropic rejected the saved key. Add a valid key or sign in to Readyset.'
        : gate.reason === 'exhausted'
          ? 'Your AI access has ended. Sign in to Readyset or add an Anthropic key.'
          : null

  const refresh = () => void invalidateTrialRelatedQueries(queryClient)

  return (
    <GateFrame title={title} notice={notice}>
      {gateError && (
        <Alert
          tone="quiet"
          variant="negative"
          icon="alert"
          label={gateError}
          className="mb-6 max-w-2xl"
        />
      )}
      <div className="max-w-2xl" data-testid="ai-setup">
        <Text as="h2" level="headline-2" className="text-content-layout-1">
          Set up AI
        </Text>
        <Text as="p" level="body-medium" className="mt-3 text-content-layout-3">
          {accountConnected
            ? 'Use the AI included with your Readyset account, or add your own Anthropic key.'
            : 'Sign in to Readyset for the included AI, or add your own Anthropic key.'}{' '}
          Charts, alerts and the Inbox keep running either way.
        </Text>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Button
            variant="rising"
            modifier="solid"
            size="large"
            label={accountConnected ? 'Use Hosted by Readyset' : 'Sign in'}
            loading={useAccount.isPending}
            onClick={signIn}
          />
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Use my own Anthropic key instead"
            onClick={() => setKeyOpen(true)}
          />
        </div>
      </div>

      <AccountLoginDialog
        isOpen={accountOpen}
        onClose={() => setAccountOpen(false)}
        onSuccess={() => {
          setAccountOpen(false)
          refresh()
        }}
      />
      <AnthropicKeyDialog
        isOpen={keyOpen}
        onClose={() => setKeyOpen(false)}
        onSignIn={() => {
          setKeyOpen(false)
          signIn()
        }}
        signInLabel={
          accountConnected
            ? 'Use Hosted by Readyset instead'
            : 'Sign in to Readyset instead'
        }
        onSuccess={refresh}
      />
    </GateFrame>
  )
}

/** Saves the user's own Anthropic key; `onSignIn` offers a Readyset account instead. */
export function AnthropicKeyDialog({
  isOpen,
  onClose,
  onSuccess,
  onSignIn,
  signInLabel,
}: {
  isOpen: boolean
  onClose: () => void
  onSuccess: () => void
  onSignIn: () => void
  signInLabel: string
}) {
  const { envRequirements, anthropicRequirement } = useTrialSource()
  const requirements = useMemo<EnvRequirement[]>(() => {
    if (anthropicRequirement) {
      return [
        {
          ...anthropicRequirement,
          accepted_names:
            anthropicRequirement.accepted_names.length > 0
              ? anthropicRequirement.accepted_names
              : ['ANTHROPIC_API_KEY'],
        },
      ]
    }
    return [
      {
        kind: 'anthropic_api_key',
        accepted_names: ['ANTHROPIC_API_KEY'],
        satisfied: false,
        source: 'missing',
        target: null,
      },
    ]
  }, [anthropicRequirement])
  return (
    <EnvSecretsDialog
      isOpen={isOpen}
      onClose={onClose}
      requirements={requirements}
      showManualAnthropicInput
      keyringAvailable={Boolean(envRequirements?.keyring_available)}
      onTrialRegister={onSignIn}
      trialActionLabel={signInLabel}
      onSuccess={onSuccess}
    />
  )
}
