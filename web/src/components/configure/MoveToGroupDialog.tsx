/**
 * Move a configured target into a group (or out of one).
 *
 * Groups are a fleet-side attribute of a target, so this writes through
 * PATCH /api/fleet/targets/{name} and invalidates the fleet target list the
 * Settings page joins against for group and tag labels.
 */

import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { updateFleetTargetGroup } from '../../lib/useFleet'

export interface MoveToGroupTarget {
  name: string
  group?: string | null
}

export function MoveToGroupDialog({
  target,
  groups,
  onClose,
  onMoved,
}: {
  /** The target being moved; null keeps the dialog closed. */
  target: MoveToGroupTarget | null
  groups: string[]
  onClose: () => void
  onMoved?: () => void
}) {
  const queryClient = useQueryClient()
  const [group, setGroup] = useState('')
  const [newGroup, setNewGroup] = useState('')
  const [moving, setMoving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setGroup(target?.group || '')
    setNewGroup('')
    setMoving(false)
    setError(null)
  }, [target?.name, target?.group])

  const save = async (remove = false) => {
    if (!target) return
    setMoving(true)
    setError(null)
    try {
      const next = remove ? null : newGroup.trim() || group || null
      await updateFleetTargetGroup(target.name, next)
      await queryClient.invalidateQueries({ queryKey: ['fleet-targets'] })
      onMoved?.()
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setMoving(false)
    }
  }

  return (
    <Modal open={!!target} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={!!target}>
        <ModalContent size="base" className="p-0 overflow-hidden">
          <ModalTitle className="sr-only">Move target to group</ModalTitle>
          <ModalDescription className="sr-only">
            Choose an existing group, create a new group, or remove this target
            from its group.
          </ModalDescription>
          <div className="p-5 border-b border-border-layout-1">
            <Text level="headline-4" className="text-content-layout-1">
              Move {target?.name} to group
            </Text>
          </div>
          <VStack className="gap-4 items-stretch p-5">
            <div>
              <Text
                level="caption"
                className="text-content-layout-3 mb-1 block"
              >
                Existing group
              </Text>
              <BaseInputSelect
                name="move-target-group"
                placeholder="Choose a group"
                value={group}
                onValueChange={(value) => {
                  setGroup(value)
                  setNewGroup('')
                }}
                options={groups.map((name) => ({ value: name, label: name }))}
              />
            </div>
            <div>
              <Text
                level="caption"
                className="text-content-layout-3 mb-1 block"
              >
                Or create a new group
              </Text>
              <BaseInputText
                name="move-target-new-group"
                value={newGroup}
                onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
                  setNewGroup(event.target.value)
                  if (event.target.value) setGroup('')
                }}
                placeholder="Group name"
              />
            </div>
            {error && (
              <Text level="body-small" className="text-content-negative-soft">
                {error}
              </Text>
            )}
            <HStack className="justify-between gap-3">
              <Button
                variant="primary"
                modifier="ghost"
                label="Remove from group"
                disabled={moving || !target?.group}
                onClick={() => void save(true)}
              />
              <HStack className="gap-2">
                <Button
                  variant="primary"
                  modifier="ghost"
                  label="Cancel"
                  onClick={onClose}
                />
                <Button
                  variant="primary"
                  modifier="solid"
                  label="Move target"
                  loading={moving}
                  disabled={moving || (!group && !newGroup.trim())}
                  onClick={() => void save()}
                />
              </HStack>
            </HStack>
          </VStack>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
