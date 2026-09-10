import { EmptyState } from '@rs/ui-new/empty-state'
import { Markdown } from '@rs/ui-new/markdown'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { UIMessage } from 'ai'
import { useEffect, useRef } from 'react'
import { getMessageContent } from '../lib/chat'

interface MessageListProps {
  messages: Array<Pick<UIMessage, 'id' | 'role' | 'parts'>>
  isStreaming?: boolean
  className?: string
}

// The answer is what the reader came for, so it carries the content tone and
// no chrome, capped at a reading measure; the echo of the question is quiet
// and short (C-07, C-08).
// Who said it is a fact, not a decoration: alignment and colour repeat it,
// they do not carry it. [C-12]
const AUTHOR_LABEL: Record<string, string> = {
  user: 'You',
  assistant: 'RDST',
  system: 'Note',
}

const BUBBLE_CLASS: Record<string, string> = {
  user: 'max-w-[80%] rounded-lg border border-border-layout-1 bg-surface-layout-2 px-4 py-2 text-content-layout-2',
  assistant: 'max-w-[65ch] text-content-layout-1',
  system:
    'max-w-[65ch] rounded-lg border border-dashed border-border-layout-1 px-4 py-2 text-content-layout-3',
}

export function MessageList({
  messages,
  isStreaming,
  className,
}: MessageListProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className={className}>
        <EmptyState
          layout="compact"
          icon="message-multiple"
          title="Nothing asked yet"
          body="Ask about the plan, the rewrites, or the index findings from this analysis."
        />
      </div>
    )
  }

  return (
    <VStack
      role="log"
      aria-live="polite"
      className={`gap-4 items-stretch ${className || ''}`}
    >
      {messages.map((msg) => (
        <div
          key={msg.id}
          className={
            msg.role === 'user'
              ? 'flex flex-col items-end'
              : msg.role === 'assistant'
                ? 'flex flex-col items-start'
                : 'flex flex-col items-center'
          }
        >
          <Text
            level="label-extra-small"
            className="mb-1 text-content-layout-3"
          >
            {AUTHOR_LABEL[msg.role] ?? AUTHOR_LABEL.system}
          </Text>
          <div className={BUBBLE_CLASS[msg.role] ?? BUBBLE_CLASS.system}>
            {msg.role === 'assistant' ? (
              <div className="text-body-medium text-content-layout-1 [&_p]:mb-3 [&_p:last-child]:mb-0 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-5">
                <Markdown options={{ disableParsingRawHTML: true }}>
                  {getMessageContent(msg)}
                </Markdown>
              </div>
            ) : (
              <Text level="body-medium">{getMessageContent(msg)}</Text>
            )}
          </div>
        </div>
      ))}
      {isStreaming && (
        <HStack
          className="items-center gap-2"
          data-testid="conversation-streaming"
        >
          <Spinner size="base" color="layout" />
          <Text level="body-small" className="text-content-layout-3">
            Thinking…
          </Text>
        </HStack>
      )}
      <div ref={messagesEndRef} />
    </VStack>
  )
}
