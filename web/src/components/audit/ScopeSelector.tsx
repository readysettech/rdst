import { BaseInputCheckboxRow } from '@rs/ui-new/base-input-checkbox'
import { Button } from '@rs/ui-new/button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { Icon } from '@rs/ui-new/icon'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Show } from '@rs/ui-new/show'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useMemo } from 'react'
import type { FleetConnectivityEvent, FleetMember } from '../../types/fleet'

function TargetRow({
  member,
  unreachable,
  checked,
  onToggle,
  disabled,
  nested = false,
}: {
  member: FleetMember
  unreachable: boolean
  checked: boolean
  onToggle: (name: string, next: boolean) => void
  disabled: boolean
  nested?: boolean
}) {
  return (
    // The row is the checkbox: one control where the user sees one row.
    <BaseInputCheckboxRow
      checked={checked}
      disabled={disabled}
      onCheckedChange={(next) => onToggle(member.name, next === true)}
      aria-label={`Select ${member.name}`}
      className={`py-2 pr-3 border-t border-border-layout-1 hover:bg-surface-layout-2/50 ${
        nested ? 'pl-10' : 'pl-3'
      }`}
    >
      <VStack className="gap-0 items-start min-w-0">
        <HStack className="gap-2 items-center">
          <Text level="label-small" className="text-content-layout-1">
            {member.name}
          </Text>
          {unreachable && (
            <Tag
              size="small"
              variant="negative"
              modifier="ghost"
              label="Unreachable"
            />
          )}
        </HStack>
        <Text level="caption" className="text-content-layout-3 truncate">
          {[member.engine, member.group].filter(Boolean).join(' · ')}
        </Text>
      </VStack>
    </BaseInputCheckboxRow>
  )
}

/** Always-visible target picker used by every health-check run. */
export function ScopeSelector({
  members,
  connectivity,
  selection,
  onSelectionChange,
  disabled = false,
  collapsed = false,
  isPending = false,
  onAddTarget,
}: {
  members: FleetMember[]
  connectivity: Record<string, FleetConnectivityEvent | undefined>
  selection: string[]
  onSelectionChange: (names: string[]) => void
  disabled?: boolean
  collapsed?: boolean
  /** The inventory has not landed yet — an empty picker means nothing. */
  isPending?: boolean
  /** Route to target setup; the only useful action when nothing is connected. */
  onAddTarget?: () => void
}) {
  const selected = useMemo(() => new Set(selection), [selection])
  // Every target holds a fixed position in the delivered inventory order. A
  // late connectivity verdict or a selection never reorders a row: an
  // unreachable target stays exactly where it is, carrying an Unreachable badge.

  const isUnreachable = (name: string) => {
    const status = connectivity[name]?.status
    return !!status && status !== 'ok' && status !== 'checking'
  }
  const groupNames = useMemo(
    () =>
      Array.from(
        new Set(
          members
            .map((member) => member.group)
            .filter((group): group is string => !!group)
        )
      ),
    [members]
  )

  const toggle = (name: string, next: boolean) => {
    if (next) {
      if (!selected.has(name)) onSelectionChange([...selection, name])
    } else {
      onSelectionChange(selection.filter((item) => item !== name))
    }
  }

  const toggleNames = (names: string[]) => {
    const allSelected = names.every((name) => selected.has(name))
    if (allSelected) {
      const namesSet = new Set(names)
      onSelectionChange(selection.filter((name) => !namesSet.has(name)))
    } else {
      onSelectionChange(Array.from(new Set([...selection, ...names])))
    }
  }

  if (collapsed) {
    return (
      <HStack className="justify-between items-center gap-3 rounded-xl border border-border-layout-1 bg-surface-layout-1 px-4 py-2.5">
        <Text level="label-small" className="text-content-layout-1">
          Targets
        </Text>
        <Text level="caption" className="text-content-layout-3">
          {selection.length} {selection.length === 1 ? 'target' : 'targets'}{' '}
          selected
        </Text>
      </HStack>
    )
  }

  const ungrouped = members.filter((member) => !member.group)
  const allSelected = members.length > 0 && selection.length === members.length
  // A settled, empty inventory is not a picker with nothing ticked — there is
  // nothing to tick, so the list is replaced by the way to connect one. [E-09]
  const noTargets = !isPending && members.length === 0

  return (
    <div className="rounded-xl border border-border-layout-1 bg-surface-layout-1 overflow-hidden">
      <HStack className="justify-between items-center gap-3 px-4 py-2.5 border-b border-border-layout-1 bg-surface-layout-2/40">
        <VStack className="gap-0 items-start">
          <Text level="label-small" className="text-content-layout-1">
            Targets
          </Text>
          <Show when={!noTargets}>
            <Text level="caption" className="text-content-layout-3">
              {isPending
                ? 'Loading targets'
                : `${selection.length} ${selection.length === 1 ? 'target' : 'targets'} selected`}
            </Text>
          </Show>
        </VStack>
        <Show when={!noTargets}>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label={allSelected ? 'Clear all' : 'Select all'}
            onClick={() =>
              onSelectionChange(
                allSelected ? [] : members.map((member) => member.name)
              )
            }
            disabled={disabled || isPending}
          />
        </Show>
      </HStack>
      <Scrollable className="max-h-72">
        <VStack className="gap-2 items-stretch p-2">
          {/* A pending inventory renders placeholder rows, never an empty
              picker under an instruction to pick something. [F-20] */}
          <Show when={isPending && members.length === 0}>
            <VStack aria-busy="true" className="gap-2 items-stretch">
              {[0, 1, 2].map((row) => (
                <Skeleton key={row} className="h-11 w-full rounded-lg" />
              ))}
            </VStack>
          </Show>
          <Show when={noTargets}>
            <EmptyState
              layout="compact"
              icon="database"
              title="No targets yet"
              body="A health check runs against the databases you have connected."
              action={
                onAddTarget
                  ? { label: 'Add a target', icon: 'add', onClick: onAddTarget }
                  : undefined
              }
            />
          </Show>
          {groupNames.map((group) => {
            const groupMembers = members.filter(
              (member) => member.group === group
            )
            const names = groupMembers.map((member) => member.name)
            const checked =
              names.length > 0 && names.every((name) => selected.has(name))
            const partial = !checked && names.some((name) => selected.has(name))
            return (
              <div
                key={group}
                data-testid={`target-group-${group}`}
                className="overflow-hidden rounded-lg border border-border-layout-1"
              >
                <BaseInputCheckboxRow
                  checked={partial ? 'indeterminate' : checked}
                  disabled={disabled}
                  onCheckedChange={() => toggleNames(names)}
                  aria-label={`Select group ${group}`}
                  className="px-3 py-2.5 bg-surface-layout-2/80 hover:bg-surface-layout-2"
                >
                  <HStack className="gap-2 items-center">
                    <Icon
                      name="layers"
                      label=""
                      aria-hidden="true"
                      className="w-4 h-4 text-content-layout-3"
                    />
                    <Text level="label-small" className="text-content-layout-1">
                      {group}
                    </Text>
                    <Text level="caption" className="text-content-layout-3">
                      {names.length} {names.length === 1 ? 'target' : 'targets'}
                    </Text>
                  </HStack>
                </BaseInputCheckboxRow>
                {groupMembers.map((member) => (
                  <TargetRow
                    key={member.name}
                    member={member}
                    unreachable={isUnreachable(member.name)}
                    checked={selected.has(member.name)}
                    onToggle={toggle}
                    disabled={disabled}
                    nested
                  />
                ))}
              </div>
            )
          })}
          <Show when={ungrouped.length > 0}>
            <div
              data-testid="target-group-ungrouped"
              className="overflow-hidden rounded-lg border border-border-layout-1"
            >
              <HStack className="gap-2 items-center px-3 py-2.5 bg-surface-layout-2/80">
                <Icon
                  name="database"
                  label=""
                  aria-hidden="true"
                  className="w-4 h-4 text-content-layout-3"
                />
                <Text level="label-small" className="text-content-layout-1">
                  Ungrouped
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  {ungrouped.length}{' '}
                  {ungrouped.length === 1 ? 'target' : 'targets'}
                </Text>
              </HStack>
              {ungrouped.map((member) => (
                <TargetRow
                  key={member.name}
                  member={member}
                  unreachable={isUnreachable(member.name)}
                  checked={selected.has(member.name)}
                  onToggle={toggle}
                  disabled={disabled}
                  nested
                />
              ))}
            </div>
          </Show>
        </VStack>
      </Scrollable>
    </div>
  )
}
