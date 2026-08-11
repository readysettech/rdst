import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Modal, ModalContentContainer } from '@rs/ui-new/modal'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import type {
  AddRelationshipData,
  SchemaTable,
  SchemaTableRelationship,
} from '../../types/schema'
import { TaskDialogContent } from '../dialog/TaskDialogContent'

interface EditingRelationship {
  sourceTable: string
  relationship: SchemaTableRelationship
}

interface SchemaAddRelationshipDialogProps {
  isOpen: boolean
  tables: SchemaTable[]
  sourceTable?: SchemaTable | null
  editingRelationship?: EditingRelationship | null
  onClose: () => void
  onSave: (data: AddRelationshipData) => Promise<boolean>
  isLoading?: boolean
}

const RELATIONSHIP_TYPES = [
  { value: 'one_to_one', label: 'One to One' },
  { value: 'one_to_many', label: 'One to Many' },
  { value: 'many_to_one', label: 'Many to One' },
  { value: 'many_to_many', label: 'Many to Many' },
]

export function SchemaAddRelationshipDialog({
  isOpen,
  tables,
  sourceTable,
  editingRelationship,
  onClose,
  onSave,
  isLoading,
}: SchemaAddRelationshipDialogProps) {
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const [joinPattern, setJoinPattern] = useState('')
  const [relationshipType, setRelationshipType] = useState('one_to_many')

  const isEditing = !!editingRelationship

  // Reset form when dialog opens/closes or editing data changes
  useEffect(() => {
    if (isOpen) {
      if (editingRelationship) {
        setSource(editingRelationship.sourceTable)
        setTarget(editingRelationship.relationship.target_table)
        setJoinPattern(editingRelationship.relationship.join_pattern)
        setRelationshipType(editingRelationship.relationship.relationship_type)
      } else {
        setSource(sourceTable?.name || '')
        setTarget('')
        setJoinPattern('')
        setRelationshipType('one_to_many')
      }
    }
  }, [isOpen, sourceTable, editingRelationship])

  const handleSave = async () => {
    if (!source.trim() || !target.trim() || !joinPattern.trim()) return

    const success = await onSave({
      source_table: source.trim(),
      target_table: target.trim(),
      join_pattern: joinPattern.trim(),
      relationship_type: relationshipType,
    })

    if (success) {
      onClose()
    }
  }

  const tableOptions = tables.map((t) => ({ value: t.name, label: t.name }))
  const isValid = source.trim() && target.trim() && joinPattern.trim()

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <TaskDialogContent
          size="base"
          icon="connect"
          title={isEditing ? 'Edit relationship' : 'Add relationship'}
          description="Describe how two tables join and relate."
          bodyClassName="space-y-4"
          footer={
            <div className="flex justify-end gap-3">
              <Button
                modifier="ghost"
                label="Cancel"
                onClick={onClose}
                disabled={isLoading}
              />
              <Button
                variant="rising"
                label={isEditing ? 'Save changes' : 'Add relationship'}
                onClick={handleSave}
                loading={isLoading}
                disabled={isLoading || !isValid}
              />
            </div>
          }
        >
          {/* Source table */}
          <div>
            <Text level="label-small" className="text-content-layout-2 mb-1">
              Source Table <span className="text-content-negative-soft">*</span>
            </Text>
            <BaseInputSelect
              name="source_table"
              value={source}
              onValueChange={setSource}
              options={tableOptions}
              placeholder="Select source table..."
              disabled={isEditing}
            />
            {isEditing && (
              <Text level="body-small" className="text-content-layout-3 mt-1">
                Source table cannot be changed when editing.
              </Text>
            )}
          </div>

          {/* Relationship type */}
          <div>
            <Text level="label-small" className="text-content-layout-2 mb-1">
              Relationship Type
            </Text>
            <BaseInputSelect
              name="relationship_type"
              value={relationshipType}
              onValueChange={setRelationshipType}
              options={RELATIONSHIP_TYPES}
              placeholder="Select relationship type..."
            />
          </div>

          {/* Target table */}
          <div>
            <Text level="label-small" className="text-content-layout-2 mb-1">
              Target Table <span className="text-content-negative-soft">*</span>
            </Text>
            <BaseInputSelect
              name="target_table"
              value={target}
              onValueChange={setTarget}
              options={tableOptions}
              placeholder="Select target table..."
              disabled={isEditing}
            />
            {isEditing && (
              <Text level="body-small" className="text-content-layout-3 mt-1">
                Target table cannot be changed when editing.
              </Text>
            )}
          </div>

          {/* Join pattern */}
          <div>
            <Text level="label-small" className="text-content-layout-2 mb-1">
              Join Pattern <span className="text-content-negative-soft">*</span>
            </Text>
            <BaseInputTextarea
              value={joinPattern}
              onChange={(e) => setJoinPattern(e.target.value)}
              placeholder="e.g., users.id = orders.user_id"
              rows={2}
              className="font-mono"
            />
            <Text level="body-small" className="text-content-layout-3 mt-1">
              SQL join condition between the two tables.
            </Text>
          </div>
        </TaskDialogContent>
      </ModalContentContainer>
    </Modal>
  )
}
