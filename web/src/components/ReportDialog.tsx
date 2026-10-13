import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  fetchQueryRegistry,
  type QueryRegistryEntry,
  type ReportRequest,
  type ReportResponse,
  type ReportSentiment,
  submitReport,
} from '../lib/api'
import { DialogShell } from './DialogShell'
import { isValidEmail, normalizeEmail } from './emailValidation'

interface ReportDialogProps {
  isOpen: boolean
  onClose: () => void
  initialQueryHash?: string
}

const sentimentOptions: Array<{ value: ReportSentiment; label: string }> = [
  { value: 'positive', label: 'Great' },
  { value: 'neutral', label: 'Okay' },
  { value: 'negative', label: 'Poor' },
]

export function ReportDialog({
  isOpen,
  onClose,
  initialQueryHash,
}: ReportDialogProps) {
  const [sentiment, setSentiment] = useState<ReportSentiment>('neutral')
  const [selectedQueryHash, setSelectedQueryHash] = useState<string | null>(
    initialQueryHash || null
  )
  const [includeQuery, setIncludeQuery] = useState(true)
  const [includePlan, setIncludePlan] = useState(true)
  const [reason, setReason] = useState('')
  const [email, setEmail] = useState('')
  const [emailOverride, setEmailOverride] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)

  // Reset form when dialog opens
  useEffect(() => {
    if (isOpen) {
      setSentiment('neutral')
      setSelectedQueryHash(initialQueryHash || null)
      setIncludeQuery(true)
      setIncludePlan(true)
      setReason('')
      setEmail('')
      setEmailOverride(false)
    }
  }, [isOpen, initialQueryHash])

  const { data: identity } = useQuery<{
    email: string | null
    verified: boolean
  } | null>({
    queryKey: ['settings', 'email'],
    queryFn: async () => {
      const response = await fetch('/api/settings/email')
      if (!response.ok) return null
      return (await response.json()) as {
        email: string | null
        verified: boolean
      }
    },
    enabled: isOpen,
    staleTime: 60_000,
  })

  const verifiedEmail =
    identity?.verified && identity.email ? identity.email : null

  useEffect(() => {
    if (isOpen && verifiedEmail && !emailOverride) {
      setEmail(verifiedEmail)
    }
  }, [isOpen, verifiedEmail, emailOverride])

  // Fetch recent queries for dropdown
  const { data: queries = [] } = useQuery<
    { queries: QueryRegistryEntry[] },
    Error,
    QueryRegistryEntry[]
  >({
    queryKey: ['queryRegistry', 10],
    queryFn: () => fetchQueryRegistry(10),
    select: (data) => data.queries,
    enabled: isOpen,
    staleTime: 30000,
  })

  // Find selected query details
  const selectedQuery = queries.find((q) => q.hash === selectedQueryHash)

  // Submit mutation
  const submitMutation = useMutation<ReportResponse, Error, ReportRequest>({
    mutationFn: (request: ReportRequest) =>
      submitReport(request).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'An error occurred')
        }
        return result
      }),
    onSuccess: () => {
      toast({
        title: 'Thank you for your feedback!',
        description: 'Your feedback helps us improve RDST.',
        variant: 'positive',
      })
      onClose()
    },
    onError: (error) => {
      toast({
        title: 'Failed to submit feedback',
        description:
          error instanceof Error ? error.message : 'An error occurred',
        variant: 'negative',
      })
    },
  })

  const handleSubmit = () => {
    if (!reason.trim()) {
      toast({
        title: 'Please enter your feedback',
        variant: 'warning',
      })
      return
    }

    const normalizedEmail = normalizeEmail(
      verifiedEmail && !emailOverride ? verifiedEmail : email
    )
    if (!isValidEmail(normalizedEmail)) {
      toast({
        title: 'Please enter a valid email address',
        variant: 'warning',
      })
      return
    }

    submitMutation.mutate({
      reason: reason.trim(),
      sentiment,
      query_hash: selectedQueryHash || undefined,
      email: normalizedEmail,
      include_query: selectedQueryHash ? includeQuery : undefined,
      include_plan: selectedQueryHash ? includePlan : undefined,
    })
  }

  const truncateSQL = (sql: string, maxLength = 50) => {
    const singleLine = sql.replace(/\s+/g, ' ').trim()
    if (singleLine.length <= maxLength) return singleLine
    return singleLine.slice(0, maxLength) + '...'
  }

  const queryLabel = (query: QueryRegistryEntry) =>
    query.tag || `Query ${query.hash.slice(0, 8)}`

  return (
    <DialogShell
      open={isOpen}
      onClose={onClose}
      title="Give feedback"
      note="Goes straight to the team."
      action={
        <>
          <Button
            variant="primary"
            modifier="ghost"
            label="Cancel"
            onClick={onClose}
            disabled={submitMutation.isPending}
          />
          <Button
            variant="primary"
            modifier="solid"
            label="Send"
            onClick={handleSubmit}
            loading={submitMutation.isPending}
            disabled={
              submitMutation.isPending ||
              !reason.trim() ||
              !(verifiedEmail && !emailOverride ? verifiedEmail : email).trim()
            }
          />
        </>
      }
    >
      <VStack className="items-stretch gap-4">
        <div>
          <Text as="p" level="caption" className="mb-1.5 text-content-layout-3">
            How was your experience?
          </Text>
          <SegmentedControl
            aria-label="How was your experience?"
            mode="radio"
            value={sentiment}
            onValueChange={setSentiment}
            segments={sentimentOptions}
          />
        </div>

        <div>
          <Text as="p" level="caption" className="mb-1.5 text-content-layout-3">
            Related query (optional)
          </Text>
          <Dropdown open={dropdownOpen} onOpenChange={setDropdownOpen}>
            <Dropdown.Trigger asChild>
              <div className="flex cursor-pointer items-center justify-between gap-2 rounded-control bg-surface-layout-2 px-3.5 py-2.5 hover:bg-surface-raised">
                {selectedQuery ? (
                  <VStack className="min-w-0 items-start gap-0">
                    <Text level="body-small" className="text-content-layout-1">
                      {queryLabel(selectedQuery)}
                    </Text>
                    <Text
                      level="mono-small"
                      className="block max-w-[280px] truncate text-content-layout-3"
                    >
                      {truncateSQL(selectedQuery.sql)}
                    </Text>
                  </VStack>
                ) : (
                  <Text level="body-small" className="text-content-layout-3">
                    None - General feedback
                  </Text>
                )}
                <Icon
                  name="chevron-down"
                  label="Select query"
                  size="small"
                  className="shrink-0 text-content-layout-3"
                />
              </div>
            </Dropdown.Trigger>
            <Dropdown.Content
              align="start"
              className="w-[calc(100%-2rem)] max-w-[432px]"
            >
              <Dropdown.Item
                label="None - General feedback"
                onClick={() => {
                  setSelectedQueryHash(null)
                  setDropdownOpen(false)
                }}
              />
              {queries.length > 0 && <Dropdown.Separator />}
              {queries.map((query: QueryRegistryEntry) => (
                <Dropdown.ItemWithChildren
                  key={query.hash}
                  onClick={() => {
                    setSelectedQueryHash(query.hash)
                    setDropdownOpen(false)
                  }}
                >
                  <div className="space-y-0.5 py-1">
                    <Text level="label-small" className="text-content-layout-1">
                      {queryLabel(query)}
                    </Text>
                    <Text
                      level="mono-small"
                      className="block max-w-full truncate text-content-layout-3"
                    >
                      {truncateSQL(query.sql, 60)}
                    </Text>
                  </div>
                </Dropdown.ItemWithChildren>
              ))}
              {queries.length === 0 && (
                <div className="px-3 py-2">
                  <Text level="body-small" className="text-content-layout-3">
                    No queries in registry
                  </Text>
                </div>
              )}
            </Dropdown.Content>
          </Dropdown>
          {selectedQueryHash && (
            <div className="mt-2">
              <HStack className="items-center justify-between py-1.5">
                <Text level="body-small" className="text-content-layout-2">
                  Query SQL
                </Text>
                <BaseInputSwitch
                  name="include-query"
                  aria-label="Query SQL"
                  checked={includeQuery}
                  onCheckedChange={setIncludeQuery}
                />
              </HStack>
              <HStack className="items-center justify-between py-1.5">
                <Text level="body-small" className="text-content-layout-2">
                  Execution plan
                </Text>
                <BaseInputSwitch
                  name="include-plan"
                  aria-label="Execution plan"
                  checked={includePlan}
                  onCheckedChange={setIncludePlan}
                />
              </HStack>
            </div>
          )}
        </div>

        <div>
          <Text as="p" level="caption" className="mb-1.5 text-content-layout-3">
            {sentiment === 'positive'
              ? 'What did RDST do well?'
              : sentiment === 'negative'
                ? 'What went wrong? How can we improve?'
                : 'Tell us about your experience'}
          </Text>
          <BaseInputTextarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Share your thoughts..."
            rows={4}
            className="resize-none"
          />
        </div>

        <div>
          <Text as="p" level="caption" className="mb-1.5 text-content-layout-3">
            Email
          </Text>
          {verifiedEmail && !emailOverride ? (
            <HStack className="flex-wrap items-center gap-x-3 gap-y-1">
              <Text level="body-small" className="text-content-layout-2">
                Sending as {verifiedEmail}
              </Text>
              <Button
                type="button"
                size="small"
                modifier="link"
                label="Use a different email"
                classMerge="h-auto p-0"
                onClick={() => setEmailOverride(true)}
              />
            </HStack>
          ) : (
            <BaseInputText
              name="feedback-email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              type="email"
              required
            />
          )}
          <Text as="p" level="caption" className="mt-1.5 text-content-layout-3">
            Required so we can follow up about your feedback
          </Text>
        </div>
      </VStack>
    </DialogShell>
  )
}
