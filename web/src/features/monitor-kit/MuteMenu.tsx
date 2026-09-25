import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { IconButton } from '@rs/ui-new/icon-button'
import { useToast } from '@rs/ui-new/use-toast'
import { type ReactNode, useState } from 'react'
import { useCreateMuteRule, useDeleteMuteRule } from '@/lib/monitor/hooks'
import type { MuteRule, MuteRuleInput } from '@/lib/monitor/types'
import { alertTypeLabel, describeRule, type MuteScope } from './severity'

const DAY = 86_400

export type MuteDuration = 'day' | 'week' | 'forever'

const DURATIONS: { value: MuteDuration; label: string }[] = [
  { value: 'day', label: 'For 1 day' },
  { value: 'week', label: 'For 1 week' },
  { value: 'forever', label: 'Until I unmute' },
]

export function muteUntil(duration: MuteDuration, now = Date.now() / 1000) {
  if (duration === 'forever') return null
  return Math.floor(now + (duration === 'day' ? DAY : 7 * DAY))
}

export interface MuteSubject {
  target: string
  /** Query the alert is about; omit for database-wide alerts. */
  queryKey?: string | null
  /** Display name for `queryKey`, used in menu copy and toasts. */
  queryLabel?: string
  /** Alert type; omit to offer only query-level muting. */
  type?: string | null
}

export function muteRuleInput(
  subject: MuteSubject,
  scope: MuteScope,
  duration: MuteDuration = 'forever'
): MuteRuleInput {
  const query = subject.queryKey ?? null
  const type = subject.type ?? null
  const until = muteUntil(duration)
  switch (scope) {
    case 'query_type':
      return {
        target: subject.target,
        query_key: query,
        type,
        until,
        note: null,
      }
    case 'query':
      return {
        target: subject.target,
        query_key: query,
        type: null,
        until,
        note: null,
      }
    case 'type_target':
      return {
        target: subject.target,
        query_key: null,
        type,
        until,
        note: null,
      }
    case 'type_all':
      return { target: null, query_key: null, type, until, note: null }
  }
}

export function muteScopes(subject: MuteSubject) {
  const scopes: { scope: MuteScope; label: string }[] = []
  const typeLabel = subject.type ? alertTypeLabel(subject.type) : ''
  if (subject.queryKey && subject.type)
    scopes.push({
      scope: 'query_type',
      label: `Mute "${typeLabel}" for this query`,
    })
  if (subject.queryKey)
    scopes.push({ scope: 'query', label: 'Mute all alerts for this query' })
  if (subject.type) {
    scopes.push({
      scope: 'type_target',
      label: `Mute "${typeLabel}" on ${subject.target}`,
    })
    scopes.push({
      scope: 'type_all',
      label: `Mute "${typeLabel}" on all targets`,
    })
  }
  return scopes
}

/** Creates a mute rule and offers an undo toast. Shared by MuteMenu and callers with their own chrome. */
export function useMuteAction() {
  const create = useCreateMuteRule()
  const remove = useDeleteMuteRule()
  const { toast } = useToast()
  const mute = (
    subject: MuteSubject,
    scope: MuteScope,
    duration: MuteDuration = 'forever',
    onMuted?: (rule: MuteRule) => void
  ) =>
    create.mutate(muteRuleInput(subject, scope, duration), {
      onSuccess: (rule) => {
        onMuted?.(rule)
        const handle = toast({
          title: 'Muted',
          description: describeRule(rule, subject.queryLabel),
          duration: 5000,
          action: (
            <Button
              label="Undo"
              size="small"
              modifier="outline"
              onClick={() => {
                remove.mutate(rule.id)
                handle.dismiss()
              }}
            />
          ),
        })
      },
      onError: (error) =>
        toast({
          title: 'Could not mute',
          description: error instanceof Error ? error.message : undefined,
          variant: 'negative',
        }),
    })
  return { mute, pending: create.isPending }
}

export interface MuteMenuProps extends MuteSubject {
  /** Items rendered after the mute options (Copy link, Mark resolved, ...). */
  extraItems?: ReactNode
  onMuted?: (rule: MuteRule) => void
  /** Accessible name of the trigger. */
  label?: string
  align?: 'start' | 'end'
  className?: string
}

/**
 * The "..." menu for an alert or a query: mute at three scopes (this query + this
 * alert type, this query, this alert type on the target or everywhere) with a
 * duration, plus caller items.
 */
export function MuteMenu({
  extraItems,
  onMuted,
  label = 'More actions',
  align = 'end',
  className,
  ...subject
}: MuteMenuProps) {
  const [duration, setDuration] = useState<MuteDuration>('forever')
  const { mute, pending } = useMuteAction()
  const scopes = muteScopes(subject)
  return (
    <Dropdown>
      <Dropdown.Trigger asChild>
        <IconButton
          icon="more"
          label={label}
          modifier="ghost"
          size="small"
          tooltip={false}
          loading={pending}
          className={className}
        />
      </Dropdown.Trigger>
      <Dropdown.Content align={align} className="min-w-72">
        {scopes.length > 0 && (
          <>
            <Dropdown.Label>Mute</Dropdown.Label>
            {scopes.map((option) => (
              <Dropdown.Item
                key={option.scope}
                leftIcon="notification"
                label={option.label}
                onSelect={() => mute(subject, option.scope, duration, onMuted)}
              />
            ))}
            <Dropdown.Separator />
            <Dropdown.Label>For how long</Dropdown.Label>
            {DURATIONS.map((option) => (
              <Dropdown.Item
                key={option.value}
                label={option.label}
                active={duration === option.value}
                rightIcon={duration === option.value ? 'tick' : undefined}
                aria-checked={duration === option.value}
                role="menuitemradio"
                onSelect={(event) => {
                  event.preventDefault()
                  setDuration(option.value)
                }}
              />
            ))}
          </>
        )}
        {extraItems && scopes.length > 0 && <Dropdown.Separator />}
        {extraItems}
      </Dropdown.Content>
    </Dropdown>
  )
}
