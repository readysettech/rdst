import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { ErrorState } from '@rs/ui-new/error-state'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useMemo, useState } from 'react'
import type { EnvRequirement } from '../lib/api'
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

function OptionRow({
  name,
  caption,
  action,
}: {
  name: string
  caption: string
  action: ReactNode
}) {
  return (
    <ListRow columns="1fr auto">
      <div className="min-w-0">
        <Text level="body-medium" className="text-content-layout-1">
          {name}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          {caption}
        </Text>
      </div>
      {action}
    </ListRow>
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
      <Text as="p" level="headline-3" className="text-content-layout-1">
        Set up AI to get fixes and the daily report.
      </Text>
      {gateError && (
        <Alert
          tone="quiet"
          variant="negative"
          icon="alert"
          label={gateError}
          className="mt-4"
        />
      )}
      <DividedList className="mt-6 max-w-2xl">
        <OptionRow
          name="Hosted by Readyset"
          caption="No API key to manage."
          action={
            <Button
              variant="primary"
              modifier="solid"
              size="small"
              label="Sign in"
              onClick={() => setAccountOpen(true)}
            />
          }
        />
        <OptionRow
          name="Use my own key"
          caption="Your provider bills you directly."
          action={
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Add key"
              onClick={() => setKeyOpen(true)}
            />
          }
        />
      </DividedList>

      <AccountLoginDialog
        isOpen={accountOpen}
        onClose={() => setAccountOpen(false)}
        onSuccess={() => {
          setAccountOpen(false)
          refresh()
        }}
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
