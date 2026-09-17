import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { ReadysetFullLogo } from '@rs/ui-new/logo'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useMemo, useState } from 'react'
import type { EnvRequirement } from '../lib/api'
import { isDesktopFrameless, isDesktopMac } from '../lib/desktop'
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../lib/trialQueries'
import type { AiGate } from '../lib/useAiGate'
import { EnvSecretsDialog } from './EnvSecretsDialog'
import { AccountLoginDialog } from './TrialRegistrationDialog'
import { WindowControls } from './WindowControls'

interface AiProviderGateProps {
  gate: Extract<AiGate, { status: 'checking' | 'error' | 'blocked' }>
}

function Benefit({ children }: { children: string }) {
  return (
    <HStack className="items-start gap-2">
      <Icon
        name="tick"
        label="Included"
        className="mt-0.5 h-4 w-4 shrink-0 text-content-positive-soft"
      />
      <Text level="body-small" className="text-content-layout-2">
        {children}
      </Text>
    </HStack>
  )
}

function GateFrame({ children }: { children: ReactNode }) {
  const frameless = isDesktopFrameless()
  const mac = isDesktopMac()

  return (
    <div className="h-dvh overflow-auto bg-surface-layout-2 text-content-layout-1">
      <header className="draggable-region sticky top-0 z-20 h-14 border-b border-border-layout-1 bg-surface-layout-1/90 backdrop-blur-md">
        <HStack
          className={`h-full items-center justify-between px-6 ${mac ? 'pl-24' : ''}`}
        >
          <ReadysetFullLogo />
          <span className="sr-only">Readyset</span>
          {frameless && <WindowControls />}
        </HStack>
      </header>
      <main className="min-h-[calc(100dvh-56px)] bg-gradient-to-b from-surface-primary-soft/20 via-surface-layout-2 to-surface-layout-2 px-6 py-10">
        {children}
      </main>
    </div>
  )
}

export function AiProviderGate({ gate }: AiProviderGateProps) {
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
      <GateFrame>
        <VStack className="mx-auto min-h-[60vh] max-w-md items-center justify-center gap-3 text-center">
          <Spinner size="base" color="primary-soft" />
          <Text level="label-medium" className="text-content-layout-1">
            Checking your AI setup
          </Text>
        </VStack>
      </GateFrame>
    )
  }

  if (gate.status === 'error') {
    return (
      <GateFrame>
        <VStack className="mx-auto min-h-[60vh] max-w-md items-center justify-center gap-4 text-center">
          <IconTile icon="alert" accent="negative" size="lg" />
          <VStack className="items-center gap-2">
            <Text as="h1" level="headline-2" className="text-content-layout-1">
              Could not check your AI setup
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              {gate.message}
            </Text>
          </VStack>
          <Button
            variant="primary"
            modifier="solid"
            label="Try again"
            onClick={() => void envRequirementsQuery.refetch()}
          />
        </VStack>
      </GateFrame>
    )
  }

  const gateError =
    gate.reason === 'invalid'
      ? 'Anthropic rejected the saved key. Add a valid key or sign in to Readyset.'
      : gate.reason === 'exhausted'
        ? 'Your included AI is no longer active. Sign in to Readyset or add an Anthropic key.'
        : null

  const refresh = () => void invalidateTrialRelatedQueries(queryClient)

  return (
    <GateFrame>
      <div className="mx-auto w-full max-w-5xl">
        <VStack className="mx-auto mb-8 max-w-2xl items-center gap-3 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-primary-soft shadow-elevation-1">
            <Icon
              name="sparkles"
              label="AI"
              className="h-7 w-7 text-content-primary-soft"
            />
          </div>
          <Text as="h1" level="headline-1" className="text-content-layout-1">
            Choose how RDST uses AI
          </Text>
          <Text level="body-medium" className="max-w-xl text-content-layout-2">
            Use the free AI included with a Readyset account, or connect your
            own Anthropic key. You can change this later in Settings.
          </Text>
        </VStack>

        {gateError && (
          <div className="mx-auto mb-5 max-w-2xl">
            <Alert variant="negative" modifier="outline" label={gateError} />
          </div>
        )}

        <div className="grid gap-5 tablet:grid-cols-2">
          <Card className="flex min-h-96 flex-col overflow-hidden border-border-primary-soft bg-surface-primary-soft/20 shadow-elevation-1">
            <Card.Header className="border-border-primary-soft/60">
              <HStack className="w-full items-start justify-between gap-3">
                <IconTile icon="sparkles" accent="primary" size="lg" />
                <Tag
                  size="small"
                  variant="positive"
                  modifier="ghost"
                  label="Free"
                />
              </HStack>
              <Card.Title className="mt-3">
                Use AI included with Readyset
              </Card.Title>
              <Card.Description>
                Create a Readyset account or sign in for free AI. Each account
                has a usage limit.
              </Card.Description>
            </Card.Header>
            <Card.Content className="flex-1 space-y-3">
              <Benefit>No API key or provider account to manage</Benefit>
              <Benefit>Available to every AI feature in RDST</Benefit>
            </Card.Content>
            <Card.Footer className="border-border-primary-soft/60">
              <Button
                variant="rising"
                modifier="solid"
                fullWidth
                label="Sign up or sign in"
                icon="arrow-right"
                iconPosition="right"
                onClick={() => setAccountOpen(true)}
              />
            </Card.Footer>
          </Card>

          <Card className="flex min-h-96 flex-col overflow-hidden">
            <Card.Header>
              <IconTile icon="key" accent="warning" size="lg" />
              <Card.Title className="mt-3">Use your Anthropic key</Card.Title>
              <Card.Description>
                Connect your own Anthropic account. RDST uses the configured
                Claude model. Anthropic bills usage to your account.
              </Card.Description>
            </Card.Header>
            <Card.Content className="flex-1 space-y-3">
              <Benefit>No Readyset account required</Benefit>
              <Benefit>Stored in your OS keychain when available</Benefit>
            </Card.Content>
            <Card.Footer>
              <Button
                variant="primary"
                modifier="outline"
                fullWidth
                label="Add Anthropic key"
                icon="key"
                iconPosition="left"
                onClick={() => setKeyOpen(true)}
              />
            </Card.Footer>
          </Card>
        </div>

        <Text
          as="p"
          level="caption"
          className="mx-auto mt-6 max-w-2xl text-center text-content-layout-3"
        >
          RDST sends schema context and your question to the selected AI
          provider. Database passwords and connection strings remain local.
        </Text>
      </div>

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
        trialActionLabel="Use free AI included with Readyset"
        onSuccess={refresh}
      />
    </GateFrame>
  )
}
