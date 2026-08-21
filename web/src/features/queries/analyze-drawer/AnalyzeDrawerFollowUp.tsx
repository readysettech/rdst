import { Button } from '@rs/ui-new/button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  AnalysisConversation,
  type AnalysisConversationContext,
  useAnalysisConversation,
} from '../../../components/AnalysisConversation'

/**
 * The Follow-up pane: the analysis conversation, hosted in the drawer instead
 * of behind a second overlay. It is the same conversation `/results` opens —
 * same hash, same history — so a thread started here continues there.
 *
 * Reached only when the query has an analysis to ask about; a record kept
 * without its body is the one case that gets this far with nothing to discuss.
 */
export function AnalyzeDrawerFollowUp({
  queryHash,
  context,
  onReRun,
}: {
  /** Absent when the stored record kept no body to ground the answers in. */
  queryHash?: string
  context?: AnalysisConversationContext
  onReRun: () => void
}) {
  if (!queryHash) {
    return (
      <EmptyState
        icon="message-multiple"
        title="This analysis has nothing to discuss"
        body="It was saved before full results were kept, so there is no plan or finding to ask about. Re-run it to ask follow-up questions."
        action={{ label: 'Run analysis again', onClick: onReRun, icon: 'play' }}
      />
    )
  }

  return <FollowUpConversation queryHash={queryHash} context={context} />
}

function FollowUpConversation({
  queryHash,
  context,
}: {
  queryHash: string
  context?: AnalysisConversationContext
}) {
  const conversation = useAnalysisConversation(queryHash, context)

  return (
    <VStack
      data-testid="analyze-drawer-follow-up"
      className="items-stretch gap-4"
    >
      <HStack className="items-center justify-between gap-2">
        <Text level="body-small" className="text-content-layout-2">
          Ask about this analysis — its plan, its rewrites, its index findings.
        </Text>
        {conversation.hasPreviousChat ? (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon="trash"
            iconPosition="left"
            label="Clear conversation"
            disabled={conversation.isLoading}
            onClick={() => void conversation.clearConversation()}
          />
        ) : null}
      </HStack>
      <AnalysisConversation conversation={conversation} />
    </VStack>
  )
}
