// Reports one piece of AI-generated output as unsafe, inappropriate, inaccurate or
// sensitive. The person reviews, and can redact, the output before it is sent; an email
// is optional, so a report can stay anonymous.
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { submitReport } from '../lib/api'
import { DialogShell } from './DialogShell'
import { isValidEmail, normalizeEmail } from './emailValidation'

const MAX_OUTPUT_CHARS = 50_000

export function ReportAIOutputDialog({
  output,
  feature,
  queryHash,
  onClose,
}: {
  output: string
  feature: string
  queryHash?: string
  onClose: () => void
}) {
  const [reviewed, setReviewed] = useState(output.slice(0, MAX_OUTPUT_CHARS))
  const [reason, setReason] = useState('')
  const [email, setEmail] = useState('')
  const normalized = normalizeEmail(email)
  const emailOk = !normalized || isValidEmail(normalized)

  const send = useMutation({
    mutationFn: async () => {
      const result = await submitReport({
        reason: reason.trim(),
        sentiment: 'negative',
        email: normalized || undefined,
        query_hash: queryHash,
        feedback_kind: 'ai_output',
        feature,
        ai_output: reviewed.trim() || undefined,
      })
      if (!result.success)
        throw new Error(result.error || "The report wasn't sent.")
    },
    onSuccess: () => {
      toast({ title: 'Report sent', variant: 'positive' })
      onClose()
    },
  })

  return (
    <DialogShell
      open
      onClose={onClose}
      title="Report AI output"
      description="Report unsafe, inappropriate, inaccurate or sensitive AI output."
      note="Goes straight to the team."
      busy={send.isPending}
      testId="report-ai-output"
      action={
        <>
          <Button
            variant="primary"
            modifier="ghost"
            label="Cancel"
            onClick={onClose}
            disabled={send.isPending}
          />
          <Button
            variant="primary"
            modifier="solid"
            label="Send report"
            onClick={() => send.mutate()}
            loading={send.isPending}
            disabled={send.isPending || !reason.trim() || !emailOk}
          />
        </>
      }
    >
      <VStack className="items-stretch gap-2.5">
        <BaseInputTextarea
          aria-label="What's wrong with it"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="What's wrong with it?"
          rows={3}
          maxLength={10_000}
          className="resize-none"
        />
        <BaseInputTextarea
          aria-label="AI output to include"
          value={reviewed}
          onChange={(e) => setReviewed(e.target.value)}
          rows={6}
          maxLength={MAX_OUTPUT_CHARS}
          className="resize-y font-mono"
        />
        <Text as="p" level="caption" className="text-content-layout-3">
          {output.length > MAX_OUTPUT_CHARS
            ? `Only the first ${MAX_OUTPUT_CHARS.toLocaleString('en-US')} characters fit. `
            : ''}
          The output above is sent as shown. It can repeat SQL, schema details
          or other database context, so remove anything you don't want to share.
        </Text>
        <BaseInputText
          name="report-email"
          aria-label="Email (optional)"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email, if you'd like a reply"
        />
        {send.error instanceof Error && (
          <Text as="p" level="caption" className="text-content-negative-soft">
            {send.error.message}
          </Text>
        )}
      </VStack>
    </DialogShell>
  )
}
