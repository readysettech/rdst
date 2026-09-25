// AI monitoring status: a database's state (AI or local only) and the account's limit.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { TrialRegistrationDialog } from '@/components/TrialRegistrationDialog'
import { releaseMonitoringSlot } from '@/lib/monitor/api'
import { useClaimAiMonitoring, useMonitoringLimit } from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import type {
  AiMonitoringReason,
  AiMonitoringState,
  MonitoringLimit,
} from '@/lib/monitor/types'
import { invalidateTrialRelatedQueries } from '@/lib/trialQueries'
import {
  aiStateCopy,
  allowanceCopy,
  FREE_A_SLOT,
  limitBasis,
  limitLine,
  MONITORING_TONE_CLASSES,
  type MonitoringStatus,
  monitoringStatus,
  NO_AI_WARNING,
  needsUserAction,
} from './aiMonitoring'
import { formatRelative } from './format'

/**
 * A database's monitoring state as a dot and words: "AI monitoring on", "Local monitoring
 * only", "Monitoring paused", "Monitoring off". `withReason` adds the AI-off reason.
 */
export function MonitoringStatusBadge({
  status,
  withReason = false,
  compact = false,
  live = false,
  className,
}: {
  status: MonitoringStatus
  withReason?: boolean
  /** RDST is collecting right now: the dot beats (still under reduced motion). */
  live?: boolean
  /** The short form ("AI on", "Local only") for tight lists. */
  compact?: boolean
  className?: string
}) {
  const tone = MONITORING_TONE_CLASSES[status.tone]
  const beating = live && status.kind !== 'off' && status.kind !== 'paused'
  return (
    <span
      data-testid="monitoring-status"
      data-kind={status.kind}
      className={cn(
        'inline-flex min-w-0 items-center gap-1.5 text-label-medium',
        tone.text,
        className
      )}
    >
      <span
        aria-hidden="true"
        data-heartbeat={beating ? 'on' : undefined}
        className={cn(
          'h-2 w-2 shrink-0 rounded-full',
          tone.dot,
          beating ? 'rdst-heartbeat' : status.busy && 'animate-pulse'
        )}
      />
      <span className="truncate">
        {compact ? status.short : status.label}
        {withReason && status.reasonLabel ? (
          <span className="text-content-layout-2"> ({status.reasonLabel})</span>
        ) : null}
      </span>
    </span>
  )
}

/**
 * The button for what the user can do about a state. Sign in, free a slot, contact us and
 * try again are handled here; turning on and checking the connection need the caller.
 */
export function MonitoringStatusAction({
  target,
  status,
  contact,
  onTurnOn,
  onCheckAgain,
  size = 'small',
}: {
  target: string
  status: MonitoringStatus
  contact?: string
  onTurnOn?: () => void
  onCheckAgain?: () => void
  size?: 'small' | 'base'
}) {
  const client = useQueryClient()
  const navigate = useNavigate()
  const claim = useClaimAiMonitoring(target)
  const [signInOpen, setSignInOpen] = useState(false)
  const action = status.action
  if (!action) return null
  const common = { size, modifier: 'outline' as const, label: action.label }
  switch (action.kind) {
    case 'turn_on':
      return onTurnOn ? <Button {...common} onClick={onTurnOn} /> : null
    case 'check_connection':
      return onCheckAgain ? <Button {...common} onClick={onCheckAgain} /> : null
    case 'retry':
      return (
        <Button
          {...common}
          loading={claim.isPending}
          onClick={() => claim.mutate()}
        />
      )
    case 'free_slot':
      return (
        <Button
          {...common}
          onClick={() =>
            void navigate({
              to: '/configure',
              search: { panel: 'connections' },
            })
          }
        />
      )
    case 'contact':
      return contact ? (
        <a
          className="text-label-medium text-content-primary-soft underline"
          href={`mailto:${contact}?subject=${encodeURIComponent('Raise my RDST AI limit')}`}
        >
          {action.label}: {contact}
        </a>
      ) : null
    case 'sign_in':
      return (
        <>
          <Button
            {...common}
            variant="rising"
            modifier="solid"
            onClick={() => setSignInOpen(true)}
          />
          <TrialRegistrationDialog
            isOpen={signInOpen}
            onClose={() => setSignInOpen(false)}
            onSuccess={() => {
              void invalidateTrialRelatedQueries(client)
              void client.invalidateQueries({ queryKey: ['monitor'] })
              void claim.mutate()
              setSignInOpen(false)
            }}
          />
        </>
      )
  }
}

const TAG_VARIANT = {
  positive: 'positive',
  warning: 'warning',
  neutral: 'neutral',
} as const

/** "AI monitoring" or "Local monitoring only", as a small tag. */
export function AiMonitoringTag({
  state,
}: {
  state: AiMonitoringState | undefined
}) {
  const copy = aiStateCopy(state)
  return (
    <Tag
      size="small"
      variant={TAG_VARIANT[copy.tone]}
      modifier="outline"
      label={copy.label}
      title={copy.description}
      data-testid="ai-monitoring-state"
      data-state={state ?? 'on'}
    />
  )
}

/** "AI monitoring on 2 of 3 databases", with which key sets the limit. */
export function MonitoringLimitLine({
  limit,
  showBasis = true,
  quiet = false,
}: {
  limit: MonitoringLimit | undefined
  showBasis?: boolean
  /** A caption under a state badge that already says AI is on. */
  quiet?: boolean
}) {
  const line = limitLine(limit)
  if (!line) return null
  const basis = showBasis ? limitBasis(limit) : null
  return (
    <div data-testid="monitoring-limit" className="space-y-0.5">
      <Text
        as="p"
        level={quiet ? 'caption' : 'label-medium'}
        className={quiet ? 'text-content-layout-3' : 'text-content-layout-1'}
      >
        {line}
      </Text>
      {basis ? (
        <Text as="p" level="caption" className="text-content-layout-3">
          {basis}
        </Text>
      ) : null}
      <SlotsElsewhere limit={limit} />
    </div>
  )
}

/**
 * Slots the account holds for databases this computer doesn't have, with a way to free
 * each. Readyset keeps a hash of each database and the name it was given, never its host.
 */
export function SlotsElsewhere({
  limit,
}: {
  limit: MonitoringLimit | undefined
}) {
  const client = useQueryClient()
  const release = useMutation({
    mutationFn: releaseMonitoringSlot,
    onSuccess: () =>
      client.invalidateQueries({ queryKey: monitorKeys.limitAll }),
  })
  const slots = limit?.elsewhere ?? []
  if (!slots.length) return null
  const now = Date.now() / 1000
  return (
    <div data-testid="slots-elsewhere" className="space-y-2 pt-2">
      <Text as="p" level="body-small" className="text-content-layout-2">
        {slots.length === 1
          ? "One AI slot is held by a database that isn't on this computer."
          : `${slots.length} AI slots are held by databases that aren't on this computer.`}{' '}
        A computer still monitoring one takes it back when a slot is free.
      </Text>
      <ul className="divide-y divide-border-layout-2 rounded-panel border border-border-layout-2">
        {slots.map((slot) => (
          <li
            key={slot.fingerprint}
            className="flex items-center justify-between gap-3 px-3 py-2"
          >
            <Text
              as="span"
              level="body-small"
              className="text-content-layout-2"
            >
              {slot.label ? (
                <span className="font-medium text-content-layout-1">
                  {slot.label}
                  {' · '}
                </span>
              ) : null}
              {slot.last_seen_at
                ? `Last active ${formatRelative(slot.last_seen_at, now)}`
                : 'Not seen recently'}
            </Text>
            <Button
              label="Release"
              modifier="outline"
              size="small"
              loading={
                release.isPending && release.variables === slot.fingerprint
              }
              onClick={() => release.mutate(slot.fingerprint)}
            />
          </li>
        ))}
      </ul>
      {release.isError ? (
        <Text as="p" level="caption" className="text-content-negative-soft">
          {release.error instanceof Error
            ? release.error.message
            : "Couldn't release that slot. Try again."}
        </Text>
      ) : null}
    </div>
  )
}

/** The account reached its AI trial limit: what's paused and who to contact. */
export function AllowanceNotice({
  limit,
}: {
  limit: MonitoringLimit | undefined
}) {
  const copy = allowanceCopy(limit)
  if (!copy) return null
  return (
    <output
      data-testid="ai-allowance"
      className="block space-y-1 rounded-panel border border-border-warning-soft bg-surface-warning-soft p-3"
    >
      <Text as="p" level="label-medium" className="text-content-warning-soft">
        {copy.title}
      </Text>
      <Text as="p" level="caption" className="text-content-layout-2">
        {copy.message}
      </Text>
      {copy.contact ? (
        <Text as="p" level="caption" className="text-content-layout-2">
          Contact us at{' '}
          <a
            className="underline"
            href={`mailto:${copy.contact}?subject=${encodeURIComponent('Raise my RDST AI limit')}`}
          >
            {copy.contact}
          </a>{' '}
          to raise it.
        </Text>
      ) : null}
    </output>
  )
}

/**
 * The AI part of one database's monitoring: its state, the account's count, and for a
 * local-only database, a way to ask again once a slot is free.
 */
export function AiMonitoringPanel({
  target,
  state,
  reason,
  since,
}: {
  target: string
  state: AiMonitoringState | undefined
  reason?: AiMonitoringReason | null
  since?: number | null
}) {
  const limit = useMonitoringLimit(undefined, state !== 'off')
  const claim = useClaimAiMonitoring(target)
  const status = monitoringStatus({ enabled: true, ai: state, reason, since })
  const stillFull = claim.isSuccess && claim.data.ai_monitoring === 'local'
  return (
    <div className="space-y-2 py-3" data-testid="ai-monitoring-panel">
      <div className="flex flex-wrap items-center gap-2">
        <Text as="p" level="label-medium" className="text-content-layout-1">
          AI
        </Text>
        <MonitoringStatusBadge status={status} />
      </div>
      {needsUserAction(status) ? (
        <Text
          as="p"
          level="body-small"
          className="text-content-warning-soft"
          data-testid="no-ai-warning"
        >
          {NO_AI_WARNING}
        </Text>
      ) : null}
      {status.kind === 'ai_on' ? null : (
        <Text as="p" level="caption" className="text-content-layout-3">
          {status.detail}
        </Text>
      )}
      {status.kind === 'local_only' && status.reason !== 'limit' ? (
        <MonitoringStatusAction
          target={target}
          status={status}
          contact={limit.data?.contact}
        />
      ) : null}
      <MonitoringLimitLine limit={limit.data} quiet />
      <AllowanceNotice limit={limit.data} />
      {state === 'local' ? (
        <div className="space-y-2">
          <Text as="p" level="caption" className="text-content-layout-3">
            {FREE_A_SLOT}
          </Text>
          <Button
            label="Turn on AI monitoring"
            size="small"
            modifier="outline"
            loading={claim.isPending}
            onClick={() => claim.mutate()}
          />
          <output className="block">
            {stillFull ? (
              <Text
                as="p"
                level="caption"
                className="text-content-warning-soft"
              >
                All AI monitoring slots are still in use.
              </Text>
            ) : null}
            {claim.isError ? (
              <Text
                as="p"
                level="caption"
                className="text-content-negative-soft"
              >
                {claim.error instanceof Error
                  ? claim.error.message
                  : "Couldn't turn on AI monitoring"}
              </Text>
            ) : null}
          </output>
        </div>
      ) : null}
    </div>
  )
}
