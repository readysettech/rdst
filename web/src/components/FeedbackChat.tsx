import { BaseInputText } from '@rs/ui-new/base-input-text'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Markdown } from '@rs/ui-new/markdown'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { trackEvent } from '../lib/analytics'
import {
  type ChatMessage,
  type ChatResponse,
  chatReport,
  type ReportKind,
  submitReport,
} from '../lib/api'
import { snoozeNudge } from '../lib/feedbackNudge'
import { DialogShell } from './DialogShell'
import { isValidEmail, normalizeEmail } from './emailValidation'

interface FeedbackChatProps {
  isOpen: boolean
  onClose: () => void
}

const FEEDBACK_NOT_SENT =
  "Couldn't send your feedback. Check your connection and try again."

const KINDS: Array<{ value: ReportKind; label: string }> = [
  { value: 'feature_request', label: 'Feature request' },
  { value: 'bug', label: 'Problem' },
  { value: 'feedback', label: 'Feedback' },
]

const STARTERS = [
  { label: 'Request a feature', text: "I'd like RDST to " },
  { label: 'Report a problem', text: "Something isn't working: " },
  { label: 'How do I...?', text: 'How do I ' },
]

type Draft = { kind: ReportKind; summary: string; drafted: boolean }

// Names like pg_stat_statements would otherwise render with italics in the middle.
const escapeIntrawordUnderscores = (markdown: string) =>
  markdown
    .split(/(`[^`]*`)/)
    .map((part, i) => (i % 2 ? part : part.replace(/(\w)_(?=\w)/g, '$1\\_')))
    .join('')

/**
 * One place to ask how RDST works and to tell the team what to fix or build.
 * The assistant answers from the product docs and, when the user says
 * something the team should hear, drafts a note; nothing reaches the team
 * until the user sends it, with the conversation attached.
 */
export function FeedbackChat({ isOpen, onClose }: FeedbackChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [email, setEmail] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (isOpen) {
      setMessages([])
      setInput('')
      setDraft(null)
    }
  }, [isOpen])

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [messages, draft])

  const { data: identity } = useQuery<{ email: string | null } | null>({
    queryKey: ['settings', 'email'],
    queryFn: async () => {
      const response = await fetch('/api/settings/email')
      return response.ok ? response.json() : null
    },
    enabled: isOpen,
    staleTime: 60_000,
  })

  useEffect(() => {
    if (isOpen && identity?.email && !email) setEmail(identity.email)
  }, [isOpen, identity?.email, email])

  const chat = useMutation<ChatResponse, Error, ChatMessage[]>({
    mutationFn: chatReport,
    onSuccess: (result, sent) => {
      setMessages([...sent, { role: 'assistant', content: result.reply }])
      if (result.suggestion) {
        setDraft({ ...result.suggestion, drafted: true })
      } else if (!result.ai && !draft) {
        // Without AI nothing gets drafted; offer the words as typed.
        const last = sent[sent.length - 1]?.content ?? ''
        setDraft({ kind: 'feedback', summary: last, drafted: false })
      }
    },
    onError: (_error, sent) => {
      setMessages(sent)
      toast({
        title: "Help isn't answering right now",
        description: 'You can still send your message to the team.',
        variant: 'warning',
      })
      const last = sent[sent.length - 1]?.content ?? ''
      setDraft({ kind: 'feedback', summary: last, drafted: false })
    },
  })

  // Thanks only once the note left the machine; otherwise the draft stays.
  const send = useMutation({
    mutationFn: async (note: Draft) => {
      const transcript = messages
        .map((m) => `${m.role === 'user' ? 'User' : 'RDST'}: ${m.content}`)
        .join('\n\n')
      const result = await submitReport({
        reason: note.summary.trim(),
        kind: note.kind,
        email: normalizeEmail(email),
        transcript: transcript || undefined,
      }).catch(() => {
        throw new Error(FEEDBACK_NOT_SENT)
      })
      if (!result.success) throw new Error(result.error || FEEDBACK_NOT_SENT)
      return note
    },
    onSuccess: (note) => {
      trackEvent('feedback_chat_sent', {
        kind: note.kind,
        drafted: note.drafted,
      })
      snoozeNudge('sent')
      toast({
        title: 'Sent to the team',
        description: "Thanks. We'll follow up by email.",
        variant: 'positive',
      })
      onClose()
    },
    onError: (error) => {
      toast({ title: error.message, variant: 'negative' })
    },
  })

  const ask = () => {
    const text = input.trim()
    if (!text || chat.isPending) return
    const next: ChatMessage[] = [...messages, { role: 'user', content: text }]
    setMessages(next)
    setInput('')
    chat.mutate(next)
  }

  const lastUserMessage = [...messages].reverse().find((m) => m.role === 'user')
  const emailOk = isValidEmail(normalizeEmail(email))

  return (
    <DialogShell
      open={isOpen}
      onClose={onClose}
      title="Ask or give feedback"
      description="Ask how to do something in RDST, report a problem or request a feature."
      note="Goes straight to the team."
      busy={send.isPending}
      testId="feedback-chat"
      action={
        draft ? (
          <>
            <Button
              variant="primary"
              modifier="ghost"
              label="Not yet"
              onClick={() => setDraft(null)}
              disabled={send.isPending}
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Send to the team"
              onClick={() => send.mutate(draft)}
              loading={send.isPending}
              disabled={send.isPending || !draft.summary.trim() || !emailOk}
            />
          </>
        ) : (
          <>
            {lastUserMessage && (
              <Button
                variant="primary"
                modifier="ghost"
                label="Send to the team"
                onClick={() =>
                  setDraft({
                    kind: 'feedback',
                    summary: lastUserMessage.content,
                    drafted: false,
                  })
                }
              />
            )}
            <Button
              variant="primary"
              modifier="solid"
              label="Ask"
              onClick={ask}
              loading={chat.isPending}
              disabled={chat.isPending || !input.trim()}
            />
          </>
        )
      }
    >
      <VStack className="items-stretch gap-4">
        {messages.length === 0 ? (
          <VStack className="items-start gap-2.5">
            <Text level="body-small" className="text-content-layout-2">
              Ask how to do something, or tell us what to fix or build next.
            </Text>
            <HStack className="flex-wrap gap-2">
              {STARTERS.map((starter) => (
                <Button
                  key={starter.label}
                  size="small"
                  variant="primary"
                  modifier="outline"
                  label={starter.label}
                  onClick={() => {
                    setInput(starter.text)
                    inputRef.current?.focus()
                  }}
                />
              ))}
            </HStack>
          </VStack>
        ) : (
          <VStack role="log" aria-live="polite" className="items-stretch gap-3">
            {messages.map((msg, i) =>
              msg.role === 'user' ? (
                <div
                  key={i}
                  className="ml-auto max-w-[85%] rounded-lg bg-surface-layout-2 px-3 py-2"
                >
                  <Text
                    level="body-small"
                    className="whitespace-pre-wrap text-content-layout-1"
                  >
                    {msg.content}
                  </Text>
                </div>
              ) : (
                <div
                  key={i}
                  className="text-body-small text-content-layout-1 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:mb-2 [&_p:last-child]:mb-0 [&_pre]:my-2 [&_pre]:overflow-x-auto [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5"
                >
                  <Markdown options={{ disableParsingRawHTML: true }}>
                    {escapeIntrawordUnderscores(msg.content)}
                  </Markdown>
                </div>
              )
            )}
            {chat.isPending && (
              <HStack className="items-center gap-2">
                <Spinner size="base" color="layout" />
                <Text level="caption" className="text-content-layout-3">
                  Thinking...
                </Text>
              </HStack>
            )}
          </VStack>
        )}

        {draft ? (
          <VStack className="items-stretch gap-2.5 rounded-lg border border-border-layout-2 p-3">
            <Text as="p" level="caption" className="text-content-layout-3">
              Send this to the team?
            </Text>
            <SegmentedControl
              aria-label="Kind"
              mode="radio"
              value={draft.kind}
              onValueChange={(kind) => setDraft({ ...draft, kind })}
              segments={KINDS}
            />
            <BaseInputTextarea
              aria-label="Note to the team"
              value={draft.summary}
              onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
              rows={3}
              className="resize-none"
            />
            <BaseInputText
              name="feedback-email"
              aria-label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
            />
            <Text as="p" level="caption" className="text-content-layout-3">
              We'll reply by email. The conversation above is attached.
            </Text>
          </VStack>
        ) : (
          <BaseInputTextarea
            ref={inputRef}
            aria-label="Message"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                ask()
              }
            }}
            placeholder="Ask a question or describe what you'd change"
            rows={2}
            className="resize-none"
          />
        )}
        <div ref={endRef} />
      </VStack>
    </DialogShell>
  )
}
