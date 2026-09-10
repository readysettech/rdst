import { ErrorState, InlineNotice } from '@rs/ui-new/error-state'
import { IconTile } from '@rs/ui-new/icon-tile'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import { AiSetupNotice, useAiBlocked } from '../components/AiSetupNotice'
import { AskPanel } from '../components/AskPanel'
import { SemanticLayerBadge } from '../components/SemanticLayerBadge'
import { TargetConnectivityNotice } from '../components/TargetConnectivityNotice'
import { TargetDropdown } from '../components/TargetDropdown'
import { TargetLockNotice } from '../components/TargetLockNotice'
import { useTargetResolution } from '../hooks/useTarget'
import { useTargetConnectivityGate } from '../lib/useTargetConnectivityGate'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'

export function AskPage({ movedFrom }: { movedFrom?: 'agents' } = {}) {
  const [movedNoticeOpen, setMovedNoticeOpen] = useState(movedFrom === 'agents')
  const { target, setTarget, isResolving, isUnavailable, refetch } =
    useTargetResolution()
  const aiBlocked = useAiBlocked()
  const passwordLock = useTargetPasswordLock(target)
  const connectivity = useTargetConnectivityGate(
    passwordLock.targetName ?? target
  )

  return (
    <div className="w-full">
      <m.header
        className="border-b border-border-layout-1 pb-5"
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="w-full items-start justify-between gap-4 flex-wrap">
          <HStack className="min-w-0 flex-1 items-center gap-4">
            <IconTile icon="sparkles" />
            <VStack className="items-start gap-1">
              <HStack className="items-center gap-3 flex-wrap">
                <Text
                  as="h1"
                  level="headline-3"
                  className="text-content-layout-1"
                >
                  Ask
                </Text>
                <SemanticLayerBadge target={target} />
              </HStack>
              <Text level="body-small" className="text-content-layout-3">
                Turn a database question into a verified answer and reusable
                SQL.
              </Text>
            </VStack>
          </HStack>
          <div className="w-full tablet:w-64">
            <TargetDropdown
              selectedTarget={target ?? null}
              onSelectTarget={(nextTarget) => setTarget(nextTarget)}
            />
          </div>
        </HStack>
      </m.header>

      <div className="space-y-4 pt-6">
        {/* A redirect that changes the mental model says so once on arrival,
            rather than silently swapping the page. [F-01] */}
        {movedNoticeOpen && (
          <InlineNotice
            accent="info"
            icon="info"
            title="Agents was retired"
            message="Ask answers database questions here. Agents themselves now run from the rdst CLI, the MCP server and the Slack bot."
            action={{
              label: 'Got it',
              onClick: () => setMovedNoticeOpen(false),
            }}
          />
        )}
        <AiSetupNotice feature="Ask" />
        {passwordLock.isLocked && (
          <TargetLockNotice
            message={passwordLock.message}
            requirements={passwordLock.missingTargetRequirements}
            keyringAvailable={passwordLock.keyringAvailable}
          />
        )}
        {!passwordLock.isLocked && (
          <TargetConnectivityNotice
            target={passwordLock.targetName ?? target}
            failure={connectivity.failure}
            isChecking={connectivity.isChecking}
            onRetry={() => void connectivity.ensureReachable()}
          />
        )}
        {/* A failed target list is named as a failure, never left to look
            like an empty history or a missing selection. [F-20, F-23] */}
        {isUnavailable ? (
          <ErrorState
            errorClass="rdst-service"
            title="Could not load your database targets"
            message="RDST could not list the configured targets, so Ask has nothing to run against yet."
            trustworthy="Nothing was asked or changed."
            onRetry={() => void refetch()}
          />
        ) : (
          <AskPanel
            target={target}
            targetResolving={isResolving}
            disabled={
              aiBlocked || passwordLock.isLocked || connectivity.isChecking
            }
            beforeRun={connectivity.ensureReachable}
          />
        )}
      </div>
    </div>
  )
}
