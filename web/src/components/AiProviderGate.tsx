import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { ErrorState } from '@rs/ui-new/error-state'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useMemo, useState } from 'react'
import type { EnvRequirement } from '../lib/api'
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../lib/trialQueries'
import type { AiGate } from '../lib/useAiGate'
import { useOpenAddDatabase } from './configure/addDatabase'
import { EnvSecretsDialog } from './EnvSecretsDialog'
import { claimFirstRunPopup, GetStarted, GetStartedButton } from './GetStarted'
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
  const { envRequirements, envRequirementsQuery, anthropicRequirement } =
    useTrialSource()
  const [accountOpen, setAccountOpen] = useState(false)
  const [keyOpen, setKeyOpen] = useState(false)
  const openAddDatabase = useOpenAddDatabase()
  // A new install opens straight into sign-in: once per app session, and only
  // when AI has never been set up (not when a saved key stopped working).
  const firstRun = gate.status === 'blocked' && gate.reason === 'missing'
  useEffect(() => {
    if (firstRun && claimFirstRunPopup('sign-in')) setAccountOpen(true)
  }, [firstRun])

  const keyRequirements = useMemo<EnvRequirement[]>(() => {
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
    gate.reason === 'invalid'
      ? 'Anthropic rejected the saved key. Add a valid key or sign in to Readyset.'
      : gate.reason === 'exhausted'
        ? 'Your AI access is no longer active. Sign in to Readyset or add an Anthropic key.'
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
      <GetStarted
        step="sign-in"
        action={
          <GetStartedButton
            label="Sign in"
            onClick={() => setAccountOpen(true)}
          />
        }
        footer={
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Use my own Anthropic key instead"
            onClick={() => setKeyOpen(true)}
          />
        }
      />

      <AccountLoginDialog
        isOpen={accountOpen}
        onClose={() => setAccountOpen(false)}
        onSuccess={() => {
          setAccountOpen(false)
          refresh()
          if (firstRun) openAddDatabase()
        }}
        getStarted={
          firstRun
            ? {
                onNext: () => {
                  setAccountOpen(false)
                  openAddDatabase()
                },
              }
            : undefined
        }
      />
      <EnvSecretsDialog
        isOpen={keyOpen}
        onClose={() => setKeyOpen(false)}
        requirements={keyRequirements}
        showManualAnthropicInput
        keyringAvailable={Boolean(envRequirements?.keyring_available)}
        onTrialRegister={() => {
          setKeyOpen(false)
          setAccountOpen(true)
        }}
        trialActionLabel="Sign in to Readyset instead"
        onSuccess={refresh}
      />
    </GateFrame>
  )
}
