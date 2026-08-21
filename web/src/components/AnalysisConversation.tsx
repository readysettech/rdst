import { Alert } from '@rs/ui-new/alert'
import { Icon } from '@rs/ui-new/icon'
import { Scrollable } from '@rs/ui-new/scrollable'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import { useEffect } from 'react'
import type { CompleteEvent } from '../lib/api'
import { useInteractiveChat } from '../lib/chat'
import { type AiGate, useAiGate } from '../lib/useAiGate'
import { MessageInput } from './MessageInput'
import { MessageList } from './MessageList'

/** The analysis a follow-up question is asked about. */
export interface AnalysisConversationContext {
  analysis_id: string
  target: string
  query_sql: string
  explain_results?: CompleteEvent['explain_results']
  llm_analysis?: CompleteEvent['llm_analysis']
}

/**
 * One analysis conversation, keyed by the analyzed query's hash — the key the
 * backend stores the thread under. `/results` and the analyze drawer's
 * Follow-up tab therefore resume the same conversation rather than each
 * keeping one of their own.
 *
 * `active` is what a surface that can be closed (a panel, an unselected tab)
 * uses to hold the reads back until it is actually showing.
 */
export function useAnalysisConversation(
  queryHash: string,
  context?: AnalysisConversationContext,
  active = true
) {
  const chat = useInteractiveChat(queryHash, context)
  const { checkStatus, loadHistory, conversationStatus } = chat

  useEffect(() => {
    if (!active) return
    checkStatus()
    loadHistory()
  }, [active, checkStatus, loadHistory])

  return {
    ...chat,
    hasPreviousChat: Boolean(
      conversationStatus?.exists && conversationStatus.totalExchanges > 0
    ),
  }
}

export type AnalysisConversationState = ReturnType<
  typeof useAnalysisConversation
>

/**
 * The AI dependency, in the place where it would otherwise be discovered by a
 * message that fails to send. Same treatment as the setup guide's key note:
 * name the problem, link to where it is fixed.
 */
function AiKeyNotice({
  gate,
}: {
  gate: Extract<AiGate, { status: 'blocked' }>
}) {
  const label =
    gate.reason === 'exhausted'
      ? 'Free AI credits are used up — add a key to keep asking'
      : gate.reason === 'invalid'
        ? 'This AI key was rejected — update it to keep asking'
        : 'Follow-up questions need an AI key — add one'

  return (
    <Link
      to="/configure"
      data-testid="conversation-ai-key-note"
      className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-content-warning-soft hover:bg-surface-warning-soft"
    >
      <Icon name="key" label="" aria-hidden="true" className="h-4 w-4" />
      <Text level="body-small" className="text-content-warning-soft">
        {label}
      </Text>
    </Link>
  )
}

/**
 * The conversation itself: history, streaming reply, composer. The surface
 * around it is the only thing that differs between `/results` and the drawer,
 * so it is the only thing left to the caller.
 */
export function AnalysisConversation({
  conversation,
  layout = 'inline',
}: {
  conversation: AnalysisConversationState
  /**
   * `'panel'` owns its height — messages scroll, the composer stays pinned.
   * `'inline'` flows inside a surface that already scrolls, such as a drawer
   * tab panel.
   */
  layout?: 'panel' | 'inline'
}) {
  const gate = useAiGate()
  const { error, input, isLoading, messages } = conversation

  const thread = (
    <>
      {error ? (
        <Alert
          variant="negative"
          modifier="outline"
          label={`Error: ${error.message}`}
          icon="alert"
          iconPosition="left"
          className="mb-4"
        />
      ) : null}
      <MessageList messages={messages} isStreaming={isLoading} />
    </>
  )

  const composer =
    gate.status === 'blocked' ? (
      <AiKeyNotice gate={gate} />
    ) : (
      <MessageInput
        value={input}
        onChange={conversation.handleInputChange}
        onSubmit={conversation.handleSubmit}
        isLoading={isLoading}
      />
    )

  if (layout === 'panel') {
    return (
      <div
        data-testid="analysis-conversation"
        className="flex min-h-0 flex-1 flex-col"
      >
        <Scrollable className="flex-1 p-4">
          <VStack className="h-full items-stretch">{thread}</VStack>
        </Scrollable>
        <div className="border-t border-border-layout-1 p-4">{composer}</div>
      </div>
    )
  }

  return (
    <VStack data-testid="analysis-conversation" className="items-stretch gap-4">
      {thread}
      {composer}
    </VStack>
  )
}
