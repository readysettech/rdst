import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { emailAuditReport, emailFleetReport } from '../lib/api'
import { isValidEmail, normalizeEmail } from './emailValidation'

// loading: waiting on the stored identity, which decides the opening step.
// confirm: a verified address is on file, one click sends.
// email: collect (or correct) the address.
// verify: the report is waiting at the keyservice for the verification click.
type Step = 'loading' | 'confirm' | 'email' | 'verify' | 'success'

type Identity = {
  email: string | null
  verified: boolean
}

interface EmailReportDialogProps {
  isOpen: boolean
  onClose: () => void
  runId: string
  kind?: 'audit' | 'fleet'
}

const POLL_INTERVAL_MS = 3000

export function EmailReportDialog({
  isOpen,
  onClose,
  runId,
  kind = 'audit',
}: EmailReportDialogProps) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<Step>('loading')
  const [email, setEmail] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)
  const [initialized, setInitialized] = useState(false)

  const { data: identity } = useQuery<Identity | null>({
    queryKey: ['settings', 'email'],
    queryFn: async () => {
      const response = await fetch('/api/settings/email')
      if (!response.ok) return null
      return (await response.json()) as Identity
    },
    enabled: isOpen,
    staleTime: 60_000,
  })

  // Pick the opening step once per open, and only once the identity has
  // resolved: a later refetch must not knock the dialog out of the step the
  // user has moved to, or overwrite what they typed.
  useEffect(() => {
    if (!isOpen) {
      setInitialized(false)
      return
    }
    if (initialized || identity === undefined) return
    setInitialized(true)
    setEmail(identity?.email ?? '')
    setStep(identity?.email && identity.verified ? 'confirm' : 'email')
  }, [isOpen, identity, initialized])

  const sendMutation = useMutation({
    mutationFn: (address?: string) =>
      kind === 'fleet'
        ? emailFleetReport(runId, address)
        : emailAuditReport(runId, address),
    onSuccess: (result) => {
      setEmail(result.email)
      if (result.status === 'sent') {
        setStep('success')
        return
      }
      if (result.status === 'verification_sent') {
        setStep('verify')
        return
      }
      toast({
        title: 'Report service unavailable',
        description: 'We could not reach the RDST report service. Try again shortly.',
        variant: 'negative',
      })
    },
  })

  const registerMutation = useMutation({
    mutationFn: async (address: string) => {
      const response = await fetch('/api/settings/email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: address }),
      })
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as {
          detail?: string
        } | null
        throw new Error(body?.detail || 'We could not save that email address.')
      }
      return address
    },
    onSuccess: (address) => {
      void queryClient.invalidateQueries({ queryKey: ['settings', 'email'] })
      sendMutation.mutate(address)
    },
  })

  // The report is already queued server-side and released the moment the link
  // is clicked, so this poll only moves the dialog to its success state.
  useEffect(() => {
    if (!isOpen || step !== 'verify') return
    let cancelled = false
    const check = async () => {
      try {
        const response = await fetch('/api/settings/email/verify-poll', {
          method: 'POST',
        })
        if (!response.ok) return
        const data = (await response.json()) as { verified: boolean }
        if (data.verified && !cancelled) {
          setStep('success')
          void queryClient.invalidateQueries({ queryKey: ['settings', 'email'] })
        }
      } catch {
        // Transient failure: the next tick retries.
      }
    }
    const timer = setInterval(() => void check(), POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [isOpen, step, queryClient])

  const loading = sendMutation.isPending || registerMutation.isPending
  const requestError =
    registerMutation.error instanceof Error
      ? registerMutation.error
      : sendMutation.error instanceof Error
        ? sendMutation.error
        : null
  const errorMessage = validationError ?? requestError?.message ?? null

  const handleClose = () => {
    setStep('loading')
    setEmail('')
    setValidationError(null)
    setInitialized(false)
    registerMutation.reset()
    sendMutation.reset()
    onClose()
  }

  const handleSend = () => {
    setValidationError(null)
    sendMutation.reset()
    sendMutation.mutate(undefined)
  }

  const handleRegister = () => {
    const address = normalizeEmail(email)
    if (!isValidEmail(address)) {
      setValidationError('Please enter a valid email address.')
      return
    }
    setValidationError(null)
    registerMutation.reset()
    sendMutation.reset()
    registerMutation.mutate(address)
  }

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && handleClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base" className="p-0 overflow-hidden">
          <ModalTitle className="sr-only">Email me this report</ModalTitle>
          <ModalDescription className="sr-only">
            Send the full report for this run to your email address.
          </ModalDescription>

          <div className="px-6 py-5 border-b border-border-layout-1 bg-surface-layout-2">
            <HStack className="gap-3 items-center">
              <div className="w-10 h-10 rounded-xl bg-surface-primary-soft flex items-center justify-center">
                <Icon
                  name="document-validation"
                  label="Report"
                  className="w-5 h-5 text-content-primary-soft"
                />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="headline-4" className="text-content-layout-1">
                  {step === 'success' ? 'Report on its way' : 'Email me this report'}
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  {step === 'verify'
                    ? 'Confirm your address to release the report.'
                    : 'We email a link to the full report, protected by a password shown in the email.'}
                </Text>
              </VStack>
            </HStack>
          </div>

          <div className="p-6 space-y-4">
            {errorMessage && (
              <Alert variant="negative" modifier="outline" label={errorMessage} />
            )}

            {step === 'loading' && (
              <Text level="body-small" className="text-content-layout-3">
                Checking your email settings...
              </Text>
            )}

            {step === 'confirm' && (
              <VStack className="gap-1 items-start">
                <Text level="body-small" className="text-content-layout-2">
                  Sending to
                </Text>
                <Text level="subtitle-2" className="text-content-layout-1">
                  {email}
                </Text>
                <button
                  type="button"
                  className="text-sm text-content-primary-soft hover:underline cursor-pointer"
                  onClick={() => setStep('email')}
                >
                  Use a different address
                </button>
              </VStack>
            )}

            {step === 'email' && (
              <div className="space-y-1">
                <Text
                  as="label"
                  level="label-small"
                  className="text-content-layout-2 block"
                >
                  Email Address
                </Text>
                <BaseInputText
                  type="email"
                  name="report-email"
                  autoComplete="email"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  disabled={loading}
                  onKeyDown={(e) => e.key === 'Enter' && handleRegister()}
                />
                <Text level="caption" className="text-content-layout-3">
                  First time on this address? We send a one-time confirmation
                  link before the report.
                </Text>
              </div>
            )}

            {step === 'verify' && (
              <div className="rounded-lg bg-surface-layout-2/60 border border-border-layout-1 px-4 py-3">
                <VStack className="gap-1 items-start">
                  <Text level="label-small" className="text-content-layout-1">
                    Confirmation email sent to {email}
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    Click the link in your inbox and the report follows
                    automatically. You can close this dialog.
                  </Text>
                </VStack>
              </div>
            )}

            {step === 'success' && (
              <div className="py-4 flex flex-col items-center text-center gap-3">
                <div className="w-14 h-14 rounded-2xl bg-surface-positive-soft flex items-center justify-center">
                  <Icon
                    name="tick"
                    label="Sent"
                    className="w-7 h-7 text-content-positive-soft"
                  />
                </div>
                <VStack className="gap-1 items-center">
                  <Text level="subtitle-2" className="text-content-layout-1">
                    Report sent to {email}
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    The email holds a link to the report and the password that
                    opens it.
                  </Text>
                </VStack>
              </div>
            )}
          </div>

          <div className="px-6 py-4 border-t border-border-layout-1 bg-surface-layout-2/40">
            <HStack className="justify-end gap-3 items-center w-full">
              {step !== 'success' && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  label="Cancel"
                  onClick={handleClose}
                  disabled={loading}
                />
              )}

              {step === 'confirm' && (
                <Button
                  variant="primary"
                  label="Send report"
                  icon="arrow-up-right"
                  iconPosition="right"
                  onClick={handleSend}
                  loading={loading}
                  disabled={loading}
                />
              )}

              {step === 'email' && (
                <Button
                  variant="primary"
                  label="Send report"
                  icon="arrow-up-right"
                  iconPosition="right"
                  onClick={handleRegister}
                  loading={loading}
                  disabled={!email || loading}
                />
              )}

              {(step === 'verify' || step === 'success') && (
                <Button variant="primary" label="Done" onClick={handleClose} />
              )}
            </HStack>
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
