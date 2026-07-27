import { BaseInputCheckbox } from '@rs/ui-new/base-input-checkbox'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useMemo, useState } from 'react'
import { splitByAvailability } from '../../lib/auditScope'
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
    <label
      className={`flex items-center gap-3 pr-3 py-2 border-t border-border-layout-1 hover:bg-surface-layout-2/50 transition-colors cursor-pointer ${
        nested ? 'pl-10' : 'pl-3'
      }`}
    >
      <BaseInputCheckbox
        checked={checked}
        onCheckedChange={(next) => onToggle(member.name, next === true)}
        disabled={disabled}
        aria-label={`Select ${member.name}`}
      />
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
    </label>
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
}: {
  members: FleetMember[]
  connectivity: Record<string, FleetConnectivityEvent | undefined>
  selection: string[]
  onSelectionChange: (names: string[]) => void
  disabled?: boolean
  collapsed?: boolean
}) {
  const [showUnavailable, setShowUnavailable] = useState(false)
  const selected = useMemo(() => new Set(selection), [selection])
  // A selected target keeps its place in the list even once a probe reports it
  // unreachable, so a late verdict never collapses a row out from under the
  // pointer. It carries an Unreachable badge instead.
  const { available, unavailable } = useMemo(() => {
    const split = splitByAvailability(members, connectivity)
    const pinned = split.unavailable.filter((member) => selected.has(member.name))
    if (pinned.length === 0) return split
    const pinnedNames = new Set(pinned.map((member) => member.name))
    return {
      available: members.filter(
        (member) =>
          pinnedNames.has(member.name) ||
          !split.unavailable.some((other) => other.name === member.name)
      ),
      unavailable: split.unavailable.filter(
        (member) => !pinnedNames.has(member.name)
      ),
    }
  }, [members, connectivity, selected])

  const isUnreachable = (name: string) => {
    const status = connectivity[name]?.status
    return !!status && status !== 'ok' && status !== 'checking'
  }
  const groupNames = useMemo(
    () =>
      Array.from(
        new Set(
          available
            .map((member) => member.group)
            .filter((group): group is string => !!group)
        )
      ),
    [available]
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

  const ungrouped = available.filter((member) => !member.group)

  return (
    <div className="rounded-xl border border-border-layout-1 bg-surface-layout-1 overflow-hidden">
      <HStack className="justify-between items-center gap-3 px-4 py-2.5 border-b border-border-layout-1 bg-surface-layout-2/40">
        <VStack className="gap-0 items-start">
          <Text level="label-small" className="text-content-layout-1">
            Targets
          </Text>
          <Text level="caption" className="text-content-layout-3">
            {selection.length} {selection.length === 1 ? 'target' : 'targets'}{' '}
            selected
          </Text>
        </VStack>
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label={
            selection.length === members.length ? 'Clear all' : 'Select all'
          }
          onClick={() =>
            onSelectionChange(
              selection.length === members.length
                ? []
                : members.map((member) => member.name)
            )
          }
          disabled={disabled || members.length === 0}
        />
      </HStack>
      <Scrollable className="max-h-72">
        <VStack className="gap-2 items-stretch p-2">
          {groupNames.map((group) => {
            const allGroupMembers = members.filter(
              (member) => member.group === group
            )
            const visibleMembers = available.filter(
              (member) => member.group === group
            )
            const names = allGroupMembers.map((member) => member.name)
            const checked =
              names.length > 0 && names.every((name) => selected.has(name))
            const partial = !checked && names.some((name) => selected.has(name))
            return (
              <div
                key={group}
                data-testid={`target-group-${group}`}
                className="overflow-hidden rounded-lg border border-border-layout-1"
              >
                <label className="flex items-center gap-3 px-3 py-2.5 bg-surface-layout-2/80 hover:bg-surface-layout-2 transition-colors cursor-pointer">
                  <BaseInputCheckbox
                    checked={partial ? 'indeterminate' : checked}
                    onCheckedChange={() => toggleNames(names)}
                    disabled={disabled}
                    aria-label={`Select group ${group}`}
                  />
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
                </label>
                {visibleMembers.map((member) => (
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
          <Show when={unavailable.length > 0}>
            <button
              type="button"
              aria-expanded={showUnavailable}
              onClick={() => setShowUnavailable((value) => !value)}
              className="flex items-center gap-1.5 px-3 py-2 cursor-pointer text-content-layout-3 hover:text-content-layout-2 transition-colors"
            >
              <Icon
                name={showUnavailable ? 'chevron-down' : 'chevron-right'}
                label=""
                aria-hidden="true"
                className="w-3.5 h-3.5"
              />
              <Text level="caption">Unavailable ({unavailable.length})</Text>
            </button>
            <Show when={showUnavailable}>
              {unavailable.map((member) => (
                <TargetRow
                  key={member.name}
                  member={member}
                  unreachable
                  checked={selected.has(member.name)}
                  onToggle={toggle}
                  disabled={disabled}
                  nested={!!member.group}
                />
              ))}
            </Show>
          </Show>
        </VStack>
      </Scrollable>
    </div>
  )
}
