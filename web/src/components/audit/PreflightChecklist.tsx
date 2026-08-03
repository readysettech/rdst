import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { EnvRequirement } from '../../lib/api'
import type { AuditPreflightResult } from '../../lib/auditPreflight'
import { TRIAL_EXHAUSTED_MESSAGE } from '../../lib/errorContract'
import { invalidateTrialRelatedQueries } from '../../lib/trialQueries'
import type { AiGate } from '../../lib/useAiGate'
import type { FleetMember } from '../../types/fleet'
import { AwsConnectionPanel } from '../aws/AwsConnectionPanel'
import { ConnectionFailureActions } from '../ConnectionFailureActions'
import { EnvSecretsDialog } from '../EnvSecretsDialog'
import { TrialRegistrationDialog } from '../TrialRegistrationDialog'

/** The audit AI gate, re-exported so callers of this checklist type one prop
 * without reaching into the hook module. */
export type AiPreflightGate = AiGate

function Check({
  ok,
  label,
  detail,
}: {
  ok: boolean
  label: string
  detail?: string
}) {
  return (
    <HStack className="gap-2 items-start">
      <Icon
        name={ok ? 'tick' : 'alert'}
        label={ok ? 'Passed' : 'Failed'}
        className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${
          ok ? 'text-content-positive-soft' : 'text-content-negative-soft'
        }`}
      />
      <Text level="caption" className="text-content-layout-2">
        {label}
      </Text>
      {detail && (
        <Text level="caption" className="text-content-layout-3">
          {detail}
        </Text>
      )}
    </HStack>
  )
}

export function PreflightChecklist({
  result,
  busy,
  onRecheck,
  liveCapture,
  aiGate,
  members,
  passwordRequirements,
  anthropicRequirement,
  keyringAvailable,
  awsProfile,
  onAwsProfileChange,
}: {
  result: AuditPreflightResult
  busy: boolean
  onRecheck: () => Promise<AuditPreflightResult | null>
  liveCapture: boolean
  aiGate: AiPreflightGate
  members: FleetMember[]
  passwordRequirements: EnvRequirement[]
  anthropicRequirement?: EnvRequirement
  keyringAvailable: boolean
  awsProfile: string
  onAwsProfileChange: (profile: string) => void
}) {
  const queryClient = useQueryClient()
  const [showTrialDialog, setShowTrialDialog] = useState(false)
  const [showKeyDialog, setShowKeyDialog] = useState(false)
  const [passwordTarget, setPasswordTarget] = useState<string | null>(null)
  const rows = Object.values(result.requirements)

  // A connection failure (auth error, timeout) is often just a wrong or
  // expired password, so any failing target can update its password — not only
  // targets whose password is missing. Reuse the target_password requirement
  // the env probe reported, or synthesize an equivalent one from the member's
  // configured password_env so a target that already has a password set can
  // still be updated.
  const passwordRequirementFor = (
    targetName: string,
    passwordEnv?: string | null
  ): EnvRequirement | null => {
    const existing = passwordRequirements.find(
      (requirement) =>
        requirement.kind === 'target_password' &&
        requirement.target === targetName
    )
    if (existing) return existing
    const envName =
      passwordEnv ??
      members.find((member) => member.name === targetName)?.password_env
    if (!envName) return null
    return {
      kind: 'target_password',
      target: targetName,
      accepted_names: [envName],
      satisfied: false,
      source: 'missing',
    }
  }
  const dockerAvailable =
    rows.length > 0 && rows.every((row) => row.docker_available)
  const aiReady = aiGate.status === 'ready' || aiGate.status === 'unverified'
  const aiDetail =
    aiGate.status === 'ready'
      ? 'Valid key or trial is ready'
      : aiGate.status === 'unverified'
        ? 'A key is configured; provider verification is temporarily unavailable'
        : aiGate.status === 'checking'
          ? 'Checking the configured key...'
          : aiGate.reason === 'exhausted'
            ? TRIAL_EXHAUSTED_MESSAGE
            : aiGate.reason === 'invalid'
              ? "The configured key isn't working"
              : 'A valid key or free trial is required'
  return (
    <div
      className="border-t border-border-layout-1 pt-4"
      data-testid="preflight-checklist"
    >
      <VStack className="gap-3 items-stretch">
        <HStack className="justify-between items-center gap-3">
          <Text level="label-small" className="text-content-layout-1">
            Preflight checklist
          </Text>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Re-check"
            loading={busy}
            onClick={() => void onRecheck()}
          />
        </HStack>
        {rows.map((row) => (
          <div
            key={row.target}
            className="rounded-lg border border-border-layout-1 bg-surface-layout-2/30 p-3"
          >
            <VStack className="gap-1.5 items-stretch">
              <Text level="label-small" className="text-content-layout-1">
                Database · {row.target}
              </Text>
              <div className="flex gap-x-5 gap-y-1 flex-wrap">
                <Check
                  ok={row.query_stats !== 'error'}
                  label="Database reachable"
                />
                <Check
                  ok={!liveCapture || row.query_stats === 'ok'}
                  label="Query statistics"
                  detail={row.query_stats === 'error' ? undefined : row.detail}
                />
              </div>
              {(row.query_stats === 'missing' || row.query_stats === 'error') &&
                liveCapture &&
                row.remediation && (
                  <div className="mt-2 rounded-lg bg-surface-warning-soft/20 border border-border-warning-soft px-3 py-2">
                    <Text
                      level="label-small"
                      className="text-content-warning-soft block mb-1"
                    >
                      How to fix this
                    </Text>
                    <pre className="whitespace-pre-wrap font-sans text-xs text-content-layout-2">
                      {row.remediation}
                    </pre>
                  </div>
                )}
              {row.query_stats === 'error' && (
                <div className="mt-1 pl-5">
                  <ConnectionFailureActions
                    failure={{
                      target: row.target,
                      message: row.detail,
                      category: row.category,
                    }}
                    passwordRequired={
                      !row.category && !!passwordRequirementFor(row.target)
                    }
                    onSetPassword={
                      passwordRequirementFor(row.target)
                        ? () => setPasswordTarget(row.target)
                        : undefined
                    }
                    onRetry={async () => {
                      const checked = await onRecheck()
                      return (
                        !!checked &&
                        !checked.errors[row.target] &&
                        checked.requirements[row.target]?.query_stats !==
                          'error'
                      )
                    }}
                  />
                </div>
              )}
            </VStack>
          </div>
        ))}
        {Object.entries(result.errors).map(([target, error]) => {
          const targetName = error.target ?? target
          const requirement = passwordRequirementFor(
            targetName,
            error.passwordEnv
          )
          return (
            <div
              key={target}
              className="rounded-lg border border-border-negative-soft bg-surface-layout-2/30 p-3"
            >
              <Text level="label-small" className="text-content-layout-1">
                Database · {target}
              </Text>
              <Check ok={false} label="Database reachable" />
              <div className="mt-2 pl-5">
                <ConnectionFailureActions
                  failure={{
                    target: targetName,
                    message: error.message,
                    code: error.code,
                  }}
                  passwordRequired={!!requirement}
                    onSetPassword={
                      requirement
                        ? () => setPasswordTarget(targetName)
                        : undefined
                    }
                  onRetry={async () => {
                    const checked = await onRecheck()
                    return !!checked && !checked.errors[targetName]
                  }}
                />
              </div>
            </div>
          )
        })}
        {rows.length > 0 && liveCapture && (
          <div className="rounded-lg border border-border-layout-1 bg-surface-layout-2/30 p-3">
            <VStack className="gap-2 items-stretch">
              <Text level="label-small" className="text-content-layout-1">
                Docker
              </Text>
              <Check
                ok={dockerAvailable}
                label="Readyset benchmark"
                detail={
                  dockerAvailable
                    ? 'Ready to run the cache benchmark'
                    : 'Required for live-capture health checks'
                }
              />
              {!dockerAvailable && (
                <div className="rounded-lg bg-surface-negative-soft/20 border border-border-negative-soft px-3 py-2">
                  <Text level="caption" className="text-content-negative-soft">
                    Start Docker Desktop, then re-check. The run stays blocked
                    until Docker is available.
                  </Text>
                </div>
              )}
            </VStack>
          </div>
        )}
        <div className="rounded-lg border border-border-layout-1 bg-surface-layout-2/30 p-3">
          <VStack className="gap-2 items-stretch">
            <Text level="label-small" className="text-content-layout-1">
              AI analysis
            </Text>
            <Check ok={aiReady} label="Anthropic access" detail={aiDetail} />
            {aiGate.status === 'blocked' && (
              <HStack className="gap-2 items-center pl-5 flex-wrap">
                {(aiGate.reason === 'missing' ||
                  aiGate.reason === 'exhausted') && (
                  <Button
                    variant="primary"
                    modifier="ghost"
                    size="small"
                    label="Start free trial"
                    onClick={() => setShowTrialDialog(true)}
                  />
                )}
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  icon="key"
                  iconPosition="left"
                  label="Set key"
                  onClick={() => setShowKeyDialog(true)}
                />
              </HStack>
            )}
          </VStack>
        </div>
        {result.aws.required && (
          <div className="rounded-lg border border-border-layout-1 bg-surface-layout-2/30 p-3">
            <VStack className="gap-3 items-stretch">
              <HStack className="gap-1.5 items-center">
                <Text level="label-small" className="text-content-layout-1">
                  AWS data
                </Text>
                <TooltipProvider delayDuration={150}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label="Why sign in to AWS?"
                        className="inline-flex rounded text-content-layout-3 hover:text-content-layout-2"
                      >
                        <Icon name="info" label="" className="h-3.5 w-3.5" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent
                      className="max-w-80 whitespace-normal"
                      label="AWS sign-in adds actual monthly cost, provisioned compute, and CloudWatch CPU metrics for more accurate sizing and savings. It is required only when AWS-hosted targets are selected."
                    />
                  </Tooltip>
                </TooltipProvider>
              </HStack>
              <Check
                ok={
                  !!result.aws.status?.has_credentials &&
                  !result.aws.error &&
                  (result.aws.mismatchedTargets?.length ?? 0) === 0
                }
                label="AWS session"
                detail={
                  result.aws.error ??
                  (result.aws.status?.has_credentials
                    ? 'Credentials are ready'
                    : 'Sign in to continue')
                }
              />
              {(!result.aws.status?.has_credentials ||
                result.aws.error ||
                (result.aws.mismatchedTargets?.length ?? 0) > 0) && (
                <div>
                  <AwsConnectionPanel
                    profile={awsProfile}
                    onProfileChange={onAwsProfileChange}
                  />
                </div>
              )}
              {(result.aws.mismatchedTargets?.length ?? 0) > 0 && (
                <div className="rounded-lg border border-border-warning-soft bg-surface-warning-soft/15 px-3 py-2">
                  <Text level="caption" className="text-content-warning-soft">
                    Health checks cannot span across multiple AWS accounts.{' '}
                    Signed in to account {result.aws.status?.account}, but{' '}
                    {result.aws.mismatchedTargets?.length === 1
                      ? 'one selected target was'
                      : `${result.aws.mismatchedTargets?.length} selected targets were`}{' '}
                    imported from account {result.aws.mismatchedAccount}. Either
                    sign in with a profile for account{' '}
                    {result.aws.mismatchedAccount}, or select only databases
                    from account {result.aws.status?.account}.
                  </Text>
                </div>
              )}
            </VStack>
          </div>
        )}
      </VStack>
      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => setShowTrialDialog(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          setShowTrialDialog(false)
        }}
      />
      {/* The key is set here, on the page that is blocked on it: saving runs
          the same invalidation as a trial activation, so the gate re-resolves
          in place instead of stranding the user on another route. */}
      <EnvSecretsDialog
        isOpen={showKeyDialog}
        onClose={() => setShowKeyDialog(false)}
        requirements={anthropicRequirement ? [anthropicRequirement] : []}
        showManualAnthropicInput
        keyringAvailable={keyringAvailable}
        onTrialRegister={() => setShowTrialDialog(true)}
        onSuccess={() => {
          setShowKeyDialog(false)
          void invalidateTrialRelatedQueries(queryClient)
        }}
      />
      <EnvSecretsDialog
        isOpen={passwordTarget !== null}
        onClose={() => setPasswordTarget(null)}
        requirements={
          passwordTarget
            ? ([passwordRequirementFor(passwordTarget)].filter(
                Boolean
              ) as EnvRequirement[])
            : []
        }
        keyringAvailable={keyringAvailable}
        onSuccess={() => {
          setPasswordTarget(null)
          void onRecheck()
        }}
      />
    </div>
  )
}
