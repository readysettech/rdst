import { Markdown } from '@rs/ui-new/markdown'
import { Spinner } from '@rs/ui-new/spinner'
import { Center, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { UIMessage } from 'ai'
import { useEffect, useRef } from 'react'
import { getMessageContent } from '../lib/chat'

interface MessageListProps {
  messages: Array<Pick<UIMessage, 'id' | 'role' | 'parts'>>
  isStreaming?: boolean
  className?: string
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
      <Center className={`flex-1 ${className || ''}`}>
        <Text level="body-medium" className="text-content-layout-3">
          No messages yet. Start a conversation!
        </Text>
      </Center>
    )
  }

  return (
    <VStack className={`gap-4 items-stretch ${className || ''}`}>
      {messages.map((msg) => {
        const alignmentClass =
          msg.role === 'user'
            ? 'flex justify-end'
            : msg.role === 'assistant'
              ? 'flex justify-start'
              : 'flex justify-center'
        const bubbleClass =
          msg.role === 'user'
            ? 'bg-surface-primary-solid text-content-primary-solid px-4 py-2 rounded-lg max-w-[80%]'
            : msg.role === 'assistant'
              ? 'bg-surface-layout-2 text-content-layout-1 px-4 py-2 rounded-lg max-w-[80%] border border-border-layout-1'
              : 'bg-surface-layout-2/60 text-content-layout-2 px-4 py-2 rounded-lg max-w-[80%] border border-dashed border-border-layout-1'

        return (
          <div key={msg.id} className={alignmentClass}>
            <div className={bubbleClass}>
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
        )
      })}
      {isStreaming && (
        <div className="flex justify-start">
          <div className="bg-surface-layout-2 text-content-layout-3 px-4 py-2 rounded-lg border border-border-layout-1 flex items-center gap-2">
            <Spinner size="base" color="layout" />
            <Text level="body-small" className="text-content-layout-3">
              Thinking...
            </Text>
          </div>
        </div>
      )}
      <div ref={messagesEndRef} />
    </VStack>
  )
}
