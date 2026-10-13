import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalTitle,
} from '@rs/ui-new/modal'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { useDeleteMuteRule, useMuteRules } from '@/lib/monitor/hooks'
import type { MuteRule } from '@/lib/monitor/types'
import { formatRelative, plural } from '../monitor-kit/format'
import {
  describeRule,
  describeRuleDuration,
  ruleIsActive,
} from '../monitor-kit/severity'

interface MuteRulesListProps {
  rules: MuteRule[] | undefined
  loading?: boolean
  names: Map<string, string>
  /** Shown when there are no rules. */
  emptyText?: string
}

/** Mute rules, each with its scope in words, how long it lasts and Unmute. */
export function MuteRulesList({
  rules,
  loading = false,
  names,
  emptyText = 'Nothing is muted. Mute an alert from its menu to silence it here.',
}: MuteRulesListProps) {
  const remove = useDeleteMuteRule()
  const { toast } = useToast()
  if (loading)
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-10 rounded-panel" />
        <Skeleton className="h-10 rounded-panel" />
      </div>
    )
  const active = (rules ?? []).filter((rule) => ruleIsActive(rule))
  if (active.length === 0)
    return (
      <Text
        level="body-small"
        className="block px-4 py-5 text-content-layout-3"
      >
        {emptyText}
      </Text>
    )
  return (
    <ul className="divide-y divide-border-layout-1">
      {active.map((rule) => {
        const queryLabel = rule.query_key
          ? names.get(rule.query_key)
          : undefined
        const unmuting = remove.isPending && remove.variables === rule.id
        return (
          <li
            key={rule.id}
            className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 transition-colors duration-fast ease-base hover:bg-surface-raised"
          >
            <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-panel bg-surface-layout-2 text-content-layout-3">
              <Icon name="eye-off" label="" className="h-3.5 w-3.5" />
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <Text level="label-large" className="text-content-layout-1">
                {describeRule(rule, queryLabel)}
              </Text>
              <Text level="caption" className="text-content-layout-3">
                {[
                  describeRuleDuration(rule),
                  rule.matched_count > 0
                    ? `silenced ${plural(rule.matched_count, 'alert')}`
                    : 'nothing silenced yet',
                  `added ${formatRelative(rule.created_at)}`,
                  rule.note,
                ]
                  .filter(Boolean)
                  .join(', ')}
              </Text>
            </div>
            <Button
              label="Unmute"
              modifier="outline"
              size="small"
              loading={unmuting}
              onClick={() =>
                remove.mutate(rule.id, {
                  onSuccess: () =>
                    toast({
                      title: 'Unmuted',
                      description: describeRule(rule, queryLabel),
                      duration: 3000,
                    }),
                  onError: (error) =>
                    toast({
                      title: 'Could not unmute',
                      description: error.message,
                      variant: 'negative',
                    }),
                })
              }
            />
          </li>
        )
      })}
    </ul>
  )
}

/** The mute rules in a dialog, each with Unmute. */
export function MuteRulesDialog({
  open,
  onClose,
  names = new Map(),
}: {
  open: boolean
  onClose: () => void
  names?: Map<string, string>
}) {
  const rules = useMuteRules()
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContentContainer open={open}>
        <ModalContent size="base" className="overflow-hidden p-0">
          <div className="shrink-0 border-b border-border-layout-2 p-5">
            <ModalTitle className="h-auto">Mute rules</ModalTitle>
          </div>
          <Scrollable rootClassName="min-h-0 flex-1" className="pb-2">
            <MuteRulesList
              rules={rules.data?.rules}
              loading={rules.isPending}
              names={names}
            />
          </Scrollable>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
