import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Modal, ModalContentContainer } from '@rs/ui-new/modal'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { activateTrial, registerTrial } from '../lib/api'
import { TaskDialogContent } from './dialog/TaskDialogContent'
import { RoutableNotice } from './RoutableNotice'

type Step = 'email' | 'verify' | 'success'
type RegisterMutationData =
  | {
      mode: 'registered'
      limitDisplay: string | null
      emailTier: string | null
    }
  | {
      mode: 'token-resent'
      emailTier: string | null
      limitCents: number | null
      remainingCents: number | null
    }

type TrialMutationError = Error & {
  didYouMean?: string
  errorCode?: string
}

interface TrialRegistrationDialogProps {
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
}

export function TrialRegistrationDialog({
  isOpen,
  onClose,
  onSuccess,
}: TrialRegistrationDialogProps) {
  const [step, setStep] = useState<Step>('email')
  const [email, setEmail] = useState('')
  const [token, setToken] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)

  // Prefill the email captured at the gate so the common case is one click,
  // but leave it fully editable: a user who gave a wrong or throwaway address
  // at the gate can correct it here, and verification runs against what they
  // type. On successful activation the backend promotes the verified address
  // to the primary identity (see TrialService.activate).
  useEffect(() => {
    if (!isOpen) return
    let cancelled = false
    void fetch('/api/settings/email')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const stored = typeof data?.email === 'string' ? data.email : null
        if (!cancelled && stored) setEmail((current) => current || stored)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [isOpen])
  const registerMutation = useMutation({
    mutationFn: async (
      registerEmail: string
    ): Promise<RegisterMutationData> => {
      const result = await registerTrial(registerEmail)
      if (result.success) {
        if (result.token_resent) {
          // Email already verified (any source): the keyservice emailed a
          // fresh link to the token page. The token itself never travels in
          // the API response - the user retrieves it from their inbox.
          return {
            mode: 'token-resent',
            emailTier: result.email_tier ?? null,
            limitCents: result.limit_cents ?? null,
            remainingCents: result.remaining_cents ?? null,
          }
        }
        return {
          mode: 'registered',
          limitDisplay: result.limit_display ?? null,
          emailTier: result.email_tier ?? null,
        }
      }
      const error = new Error(
        result.detail ?? 'Registration failed.'
      ) as TrialMutationError
      error.didYouMean = result.did_you_mean ?? undefined
      // Preserve the machine-readable code so the UI can branch a capacity
      // limit (PROGRAM_FULL) or a rate limit (RATE_LIMITED) to a real next
      // move instead of failing the same button again.
      error.errorCode = result.error_code ?? undefined
      throw error
    },
    onSuccess: () => {
      setStep('verify')
    },
  })
  const activateMutation = useMutation({
    mutationFn: ({
      token,
      email,
      emailTier,
      limitCents,
      remainingCents,
    }: {
      token: string
      email: string
      emailTier?: string | null
      limitCents?: number | null
      remainingCents?: number | null
    }) =>
      activateTrial(token, email, {
        emailTier: emailTier ?? undefined,
        limitCents: limitCents ?? undefined,
        remainingCents: remainingCents ?? undefined,
      }).then((result) => {
        if (!result.success) {
          throw new Error(result.message ?? 'Activation failed.')
        }
        return result
      }),
    onSuccess: () => {
      setStep('success')
      onSuccess?.()
    },
  })
  const loading = registerMutation.isPending || activateMutation.isPending
  const registerError =
    registerMutation.error instanceof Error ? registerMutation.error : null
  const activateError =
    activateMutation.error instanceof Error ? activateMutation.error : null
  const trialErrorCode =
    registerError && 'errorCode' in registerError
      ? ((registerError as TrialMutationError).errorCode ?? null)
      : null
  const isProgramFull = trialErrorCode === 'PROGRAM_FULL'
  const isRateLimited = trialErrorCode === 'RATE_LIMITED'
  // Only surface the generic error Alert for failures we don't branch below.
  const errorMessage =
    validationError ??
    activateError?.message ??
    (isProgramFull || isRateLimited ? null : (registerError?.message ?? null))
  const rateLimitMessage =
    isRateLimited && registerError ? registerError.message : null
  const didYouMean =
    registerError && 'didYouMean' in registerError
      ? ((registerError as TrialMutationError).didYouMean ?? null)
      : null
  const tokenResent = registerMutation.data?.mode === 'token-resent'
  const limitDisplay =
    registerMutation.data?.mode === 'registered'
      ? registerMutation.data.limitDisplay
      : null
  const emailTier = registerMutation.data?.emailTier ?? null
  const resentBalance =
    registerMutation.data?.mode === 'token-resent'
      ? registerMutation.data
      : null
  // The verify step has two modes — a fresh verification email vs a re-sent
  // token link — that differ only in copy. Select one set up front instead of
  // branching every line.
  const verifyCopy = tokenResent
    ? {
        title: "You're already registered",
        subtitle: "This email already has a trial — we've re-sent your token.",
        banner: `Trial token re-sent to ${email}`,
        heading: 'Your token is on its way:',
        steps: [
          '1. Check your inbox for "Your RDST trial token"',
          '2. Open the link to view your token',
          '3. Paste the token below',
        ],
      }
    : {
        title: 'Check your email',
        subtitle: 'Paste the trial token from the verification email.',
        banner: `Verification email sent to ${email}`,
        heading: 'Steps to get your token:',
        steps: [
          '1. Check your email (including spam folder)',
          '2. Click the verification link',
          '3. Copy the trial token from the page',
        ],
      }
  const dialogTitle =
    step === 'email'
      ? 'Start free trial'
      : step === 'verify'
        ? verifyCopy.title
        : 'Trial activated'
  const dialogDescription =
    step === 'email'
      ? 'Get free AI analysis credits — no credit card required.'
      : step === 'verify'
        ? verifyCopy.subtitle
        : 'Your free trial is ready to use.'

  const reset = () => {
    setStep('email')
    setEmail('')
    setToken('')
    setValidationError(null)
    registerMutation.reset()
    activateMutation.reset()
  }

  const handleClose = () => {
    reset()
    onClose()
  }

  const handleRegister = () => {
    if (!email || !email.includes('@')) {
      setValidationError('Please enter a valid email address.')
      return
    }

    setValidationError(null)
    registerMutation.reset()
    activateMutation.reset()
    registerMutation.mutate(email)
  }

  const handleActivate = () => {
    if (!token || token.trim().length < 10) {
      setValidationError(
        'Please paste a valid trial token (at least 10 characters).'
      )
      return
    }

    setValidationError(null)
    activateMutation.reset()
    activateMutation.mutate({
      token: token.trim(),
      email,
      emailTier,
      limitCents: resentBalance?.limitCents ?? null,
      remainingCents: resentBalance?.remainingCents ?? null,
    })
  }

  const handleDidYouMean = () => {
    if (didYouMean) {
      setEmail(didYouMean)
      setValidationError(null)
      registerMutation.reset()
    }
  }

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && handleClose()}>
      <ModalContentContainer open={isOpen}>
        <TaskDialogContent
          size="base"
          icon="sparkles"
          title={dialogTitle}
          description={dialogDescription}
          bodyClassName="space-y-4"
          footer={
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

              {step === 'email' && (
                <Button
                  variant="rising"
                  label="Start free trial"
                  icon="arrow-right"
                  iconPosition="right"
                  onClick={handleRegister}
                  loading={loading}
                  disabled={!email || loading}
                />
              )}

              {step === 'verify' && (
                <Button
                  variant="rising"
                  label="Activate"
                  icon="tick"
                  iconPosition="right"
                  onClick={handleActivate}
                  loading={loading}
                  disabled={!token || loading}
                />
              )}

              {step === 'success' && (
                <Button variant="primary" label="Done" onClick={handleClose} />
              )}
            </HStack>
          }
        >
          {errorMessage && (
            <Alert variant="negative" modifier="outline" label={errorMessage} />
          )}

          {/* Branched trial dead-ends: a capacity limit routes to own-key
                entry (a real alternative); a rate limit states the cause
                without a misleading countdown. (onboarding F8) */}
          {isProgramFull && (
            <RoutableNotice
              kind="key-needed"
              title="The free trial is full right now"
              message="Add your own Anthropic API key instead to keep using AI analysis."
              onBeforeRoute={handleClose}
            />
          )}
          {isRateLimited && (
            <Alert
              variant="warning"
              modifier="outline"
              label={
                rateLimitMessage ??
                "You've signed up for too many accounts recently."
              }
            />
          )}

          {step === 'email' && (
            <>
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
                  name="trial-email"
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
                  Business emails get more credits. We only use this to send the
                  trial token.
                </Text>
              </div>
              {didYouMean && (
                <Button
                  variant="primary"
                  modifier="link"
                  size="small"
                  className="px-0"
                  label={`Did you mean ${didYouMean}?`}
                  onClick={handleDidYouMean}
                />
              )}
              {/* The own-key path must always be reachable, not only when
                    the trial is at capacity. */}
              {!isProgramFull && (
                <RoutableNotice
                  kind="key-needed"
                  title="Already have an Anthropic key?"
                  message="Add your own key instead — no trial needed."
                  onBeforeRoute={handleClose}
                />
              )}
            </>
          )}

          {step === 'verify' && (
            <>
              <div className="rounded-lg bg-surface-positive-soft/20 border border-border-positive-soft px-4 py-3">
                <VStack className="gap-1 items-start">
                  <Text
                    level="label-small"
                    className="text-content-positive-soft"
                  >
                    {verifyCopy.banner}
                  </Text>
                  {limitDisplay && !tokenResent && (
                    <Text level="body-small" className="text-content-layout-2">
                      Your trial credit: {limitDisplay}
                      {emailTier === 'business'
                        ? ' (business email)'
                        : ' (personal email)'}
                    </Text>
                  )}
                </VStack>
              </div>

              <div className="rounded-lg bg-surface-layout-2/60 border border-border-layout-1 px-4 py-3">
                <VStack className="gap-1 items-start">
                  <Text level="label-small" className="text-content-layout-1">
                    {verifyCopy.heading}
                  </Text>
                  {verifyCopy.steps.map((line) => (
                    <Text
                      key={line}
                      level="body-small"
                      className="text-content-layout-3"
                    >
                      {line}
                    </Text>
                  ))}
                </VStack>
              </div>

              <div className="space-y-1">
                <Text
                  as="label"
                  level="label-small"
                  className="text-content-layout-2 block"
                >
                  Trial Token
                </Text>
                <BaseInputText
                  type="text"
                  name="trial-token"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  data-1p-ignore="true"
                  data-lpignore="true"
                  data-form-type="other"
                  style={{ WebkitTextSecurity: 'disc' } as React.CSSProperties}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="Paste your trial token here"
                  disabled={loading}
                  onKeyDown={(e) => e.key === 'Enter' && handleActivate()}
                />
              </div>
            </>
          )}

          {step === 'success' && (
            <div className="py-4 flex flex-col items-center text-center gap-3">
              <div className="w-14 h-14 rounded-2xl bg-surface-positive-soft flex items-center justify-center">
                <Icon
                  name="tick"
                  label="Success"
                  className="w-7 h-7 text-content-positive-soft"
                />
              </div>
              <VStack className="gap-1 items-center">
                <Text level="subtitle-2" className="text-content-layout-1">
                  Trial is active
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  You can now use AI analysis features. Your balance will appear
                  in the sidebar.
                </Text>
              </VStack>
            </div>
          )}
        </TaskDialogContent>
      </ModalContentContainer>
    </Modal>
  )
}
