// The one AI question in builds that ask before sending anything to AI (the Microsoft Store
// build), the words for what AI sees, and what turning it off costs. The question is asked
// where AI starts (turning on monitoring) or where a feature needs it, never on launch.
import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Icon } from '@rs/ui-new/icon'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import { PRIVACY_POLICY_URL } from '../features/settings/links'
import { useAiConsent } from '../lib/aiConsent'
import { thisComputer } from '../lib/desktop'

/** What AI sees, and where it goes. Shown wherever the user is asked to allow AI. */
export function AiDisclosure({ className }: { className?: string }) {
  return (
    <section
      aria-label="What RDST's AI sees"
      data-testid="ai-disclosure"
      className={`rounded-panel border border-border-layout-2 px-4 py-3 ${className ?? ''}`}
    >
      <Text
        as="p"
        level="label-medium"
        className="flex items-center gap-2 text-content-layout-1"
      >
        <Icon
          name="sparkles"
          label=""
          size="small"
          className="shrink-0 text-content-primary-soft"
        />
        RDST uses AI to find slow queries and write fixes
      </Text>
      <Text as="p" level="body-small" className="mt-2 text-content-layout-2">
        To do that, it sends an AI model:
      </Text>
      <ul className="mt-1 list-disc pl-5 text-body-small text-content-layout-2">
        <li>SQL text and query plans</li>
        <li>Table schemas, column statistics and a few sample values</li>
        <li>Your Ask questions and their answers</li>
      </ul>
      <Text as="p" level="body-small" className="mt-2 text-content-layout-2">
        It goes to Readyset's AI service and the model providers it uses, or
        straight to Anthropic if you use your own key. You can turn AI off any
        time in Settings.{' '}
        <a
          className="text-content-layout-1 underline underline-offset-2"
          href={PRIVACY_POLICY_URL}
          target="_blank"
          rel="noreferrer"
        >
          Privacy Policy
        </a>
      </Text>
    </section>
  )
}

const STOPS = [
  'Slow-query and regression alerts',
  'Fixes and index suggestions',
  'Ask and query analysis',
  'Nightly report summaries',
]
const KEEPS = [
  'Live charts and query history',
  'Alerts when the database is unreachable',
  'Stuck queries, lock waits and connection limits',
]

/** What stops and what keeps working with AI off, side by side. */
export function AiOffComparison() {
  const column = (title: string, items: string[], tone: string) => (
    <div className="rounded-panel border border-border-layout-2 bg-surface-layout-2 px-3.5 py-3">
      <Text as="p" level="label-small" className={tone}>
        {title}
      </Text>
      <ul className="mt-1.5 list-disc space-y-1 pl-4 text-body-small text-content-layout-2">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
  return (
    <div
      className="grid gap-2.5 tablet:grid-cols-2"
      data-testid="ai-off-comparison"
    >
      {column('Turned off', STOPS, 'text-content-negative-soft')}
      {column('Still works', KEEPS, 'text-content-positive-soft')}
    </div>
  )
}

/** Asks to turn AI on. Closing it leaves AI as it was. */
export function AllowAiDialog({
  isOpen,
  onClose,
  onAllowed,
}: {
  isOpen: boolean
  onClose: () => void
  onAllowed?: () => void
}) {
  const ai = useAiConsent()
  const { resetError } = ai
  useEffect(() => {
    if (isOpen) resetError()
  }, [isOpen, resetError])
  return (
    <ConfirmDialog
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={() =>
        void ai.decide('granted').then(
          () => {
            onClose()
            onAllowed?.()
          },
          () => {}
        )
      }
      tone="quiet"
      title="Turn on AI?"
      confirmLabel="Turn on AI"
      confirmVariant="primary"
      cancelLabel="Not now"
      loading={ai.deciding}
      blockCloseWhileLoading
      note={ai.error ? "Couldn't save the choice. Try again." : undefined}
    >
      <AiDisclosure />
    </ConfirmDialog>
  )
}

/** Confirms turning AI off, saying what stops. */
export function TurnOffAiDialog({
  isOpen,
  onClose,
}: {
  isOpen: boolean
  onClose: () => void
}) {
  const ai = useAiConsent()
  const { resetError } = ai
  useEffect(() => {
    if (isOpen) resetError()
  }, [isOpen, resetError])
  return (
    <ConfirmDialog
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={() => void ai.decide('denied').then(onClose, () => {})}
      tone="quiet"
      title="Turn off AI?"
      subtitle="RDST stops sending anything to AI models right away. Requests already sent can't be recalled."
      confirmLabel="Turn off AI"
      confirmVariant="negative"
      loading={ai.deciding}
      blockCloseWhileLoading
      note={ai.error ? "Couldn't save the choice. Try again." : undefined}
    >
      <AiOffComparison />
    </ConfirmDialog>
  )
}

/** What each page that needs AI says while AI is off. */
const FEATURE_COPY: Record<string, { title: string; detail: string }> = {
  '/ask': {
    title: 'Ask needs AI',
    detail:
      'Ask writes SQL for your question with an AI model, which sees the question, your schema and a few sample values.',
  },
  '/results': {
    title: 'Query analysis needs AI',
    detail:
      "Analysis sends the query, its plan and table statistics to an AI model to find what's slow and how to fix it.",
  },
}
const FALLBACK_COPY = {
  title: 'This needs AI',
  detail: 'This page sends your SQL and schema to an AI model.',
}

/** Stands in for a page that needs AI while AI is off. */
export function AiOffGate({ path, title }: { path: string; title: string }) {
  const [open, setOpen] = useState(false)
  const copy = FEATURE_COPY[path] ?? FALLBACK_COPY
  return (
    <>
      <PageBar title={title} />
      <PageContainer className="pt-16 pb-24">
        <div
          className="mx-auto flex max-w-md flex-col items-center gap-3 text-center"
          data-testid="ai-off-gate"
        >
          <span className="flex h-11 w-11 items-center justify-center rounded-panel bg-surface-primary-soft">
            <Icon
              name="sparkles"
              label=""
              className="text-content-primary-soft"
            />
          </span>
          <Text as="h2" level="headline-3" className="text-content-layout-1">
            {copy.title}
          </Text>
          <Text as="p" level="body-medium" className="text-content-layout-2">
            {copy.detail} AI is off on {thisComputer()}.
          </Text>
          <Button
            className="mt-2"
            variant="primary"
            modifier="solid"
            label="Turn on AI"
            onClick={() => setOpen(true)}
          />
        </div>
      </PageContainer>
      <AllowAiDialog isOpen={open} onClose={() => setOpen(false)} />
    </>
  )
}

/** A line above a list AI fills, while AI is off: what still arrives, and the way back. */
export function AiOffNotice({ className }: { className?: string }) {
  const ai = useAiConsent()
  const [open, setOpen] = useState(false)
  if (!ai.off) return null
  return (
    <div
      data-testid="ai-off-notice"
      className={`flex flex-wrap items-center gap-3 rounded-panel border border-border-layout-2 px-4 py-2.5 ${className ?? ''}`}
    >
      <span
        aria-hidden
        className="h-2 w-2 shrink-0 rounded-full bg-surface-warning-solid"
      />
      <Text as="p" level="body-small" className="flex-1 text-content-layout-2">
        AI is off. Alerts for connections, stuck queries and locks still arrive;
        slow-query alerts and fixes don't.
      </Text>
      <Button
        size="small"
        modifier="outline"
        label="Turn on AI"
        onClick={() => setOpen(true)}
      />
      <AllowAiDialog isOpen={open} onClose={() => setOpen(false)} />
    </div>
  )
}
