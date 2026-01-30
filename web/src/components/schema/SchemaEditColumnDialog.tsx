import { useState, useEffect } from 'react'
import { Text } from '@rs/ui-new/text'
import { Button } from '@rs/ui-new/button'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalTitle,
} from '@rs/ui-new/modal'
import type { SchemaTableColumn, AddColumnData } from '../../types/schema'

interface SchemaEditColumnDialogProps {
  isOpen: boolean
  tableName: string
  column: SchemaTableColumn | null
  onClose: () => void
  onSave: (data: AddColumnData) => Promise<boolean>
  isLoading?: boolean
}

export function SchemaEditColumnDialog({
  isOpen,
  tableName,
  column,
  onClose,
  onSave,
  isLoading,
}: SchemaEditColumnDialogProps) {
  const [description, setDescription] = useState('')
  const [unit, setUnit] = useState('')
  const [isPii, setIsPii] = useState(false)

  useEffect(() => {
    if (column) {
      setDescription(column.description || '')
      setUnit(column.unit || '')
      setIsPii(column.is_pii || false)
    }
  }, [column])

  if (!column) return null

  const handleSave = async () => {
    const success = await onSave({
      table_name: tableName,
      column_name: column.name,
      description,
      unit: unit || undefined,
      is_pii: isPii,
    })
    if (success) {
      onClose()
    }
  }

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base" className="gap-6">
          <ModalTitle>Edit Column</ModalTitle>

          <div className="space-y-4">
            {/* Column info */}
            <div className="bg-surface-layout-1 rounded-lg p-3">
              <Text level="label-small" className="text-content-layout-2">
                Column
              </Text>
              <div className="flex items-center gap-2 mt-1">
                <code className="text-sm font-mono text-content-layout-1">
                  {tableName}.{column.name}
                </code>
                {column.data_type && (
                  <span className="text-xs text-content-layout-3">({column.data_type})</span>
                )}
              </div>
            </div>

            {/* Description */}
            <div>
              <Text level="label-small" className="text-content-layout-2 mb-1">
                Description
              </Text>
              <BaseInputTextarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe what this column represents..."
                rows={3}
              />
              <Text level="body-small" className="text-content-layout-3 mt-1">
                Describe the business meaning of this column.
              </Text>
            </div>

            {/* Unit */}
            <div>
              <Text level="label-small" className="text-content-layout-2 mb-1">
                Unit
              </Text>
              <BaseInputText
                value={unit}
                onChange={(e) => setUnit(e.target.value)}
                placeholder="e.g., USD, kg, seconds, percent"
              />
              <Text level="body-small" className="text-content-layout-3 mt-1">
                Unit of measurement for numeric columns.
              </Text>
            </div>

            {/* PII flag */}
            <div className="flex items-center gap-3">
              <BaseInputSwitch
                name="is_pii"
                checked={isPii}
                onCheckedChange={setIsPii}
              />
              <div className="flex-1">
                <Text level="label-small" className="text-content-layout-1">
                  Contains PII
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  Mark if this column contains personally identifiable information.
                </Text>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3">
            <Button modifier="ghost" label="Cancel" onClick={onClose} disabled={isLoading} />
            <Button
              label={isLoading ? 'Saving...' : 'Save'}
              onClick={handleSave}
              loading={isLoading}
              disabled={isLoading}
            />
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
