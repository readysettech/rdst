import { useState, useEffect } from 'react'
import { Text } from '@rs/ui-new/text'
import { Button } from '@rs/ui-new/button'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalTitle,
} from '@rs/ui-new/modal'
import type { SchemaTable, AddTableData } from '../../types/schema'

interface SchemaEditTableDialogProps {
  isOpen: boolean
  table: SchemaTable | null
  onClose: () => void
  onSave: (data: AddTableData) => Promise<boolean>
  isLoading?: boolean
}

export function SchemaEditTableDialog({
  isOpen,
  table,
  onClose,
  onSave,
  isLoading,
}: SchemaEditTableDialogProps) {
  const [description, setDescription] = useState('')
  const [businessContext, setBusinessContext] = useState('')

  useEffect(() => {
    if (table) {
      setDescription(table.description || '')
      setBusinessContext(table.business_context || '')
    }
  }, [table])

  if (!table) return null

  const handleSave = async () => {
    const success = await onSave({
      table_name: table.name,
      description,
      business_context: businessContext || undefined,
    })
    if (success) {
      onClose()
    }
  }

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base" className="gap-6">
          <ModalTitle>Edit Table</ModalTitle>

          <div className="space-y-4">
            {/* Table info */}
            <div className="bg-surface-layout-1 rounded-lg p-3">
              <Text level="label-small" className="text-content-layout-2">
                Table
              </Text>
              <code className="text-sm font-mono text-content-layout-1 mt-1 block">
                {table.name}
              </code>
              {table.row_estimate && (
                <Text level="body-small" className="text-content-layout-3 mt-1">
                  ~{table.row_estimate} rows
                </Text>
              )}
            </div>

            {/* Description */}
            <div>
              <Text level="label-small" className="text-content-layout-2 mb-1">
                Description
              </Text>
              <BaseInputTextarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe what this table contains..."
                rows={3}
              />
              <Text level="body-small" className="text-content-layout-3 mt-1">
                A brief description of what data this table stores.
              </Text>
            </div>

            {/* Business Context */}
            <div>
              <Text level="label-small" className="text-content-layout-2 mb-1">
                Business Context
              </Text>
              <BaseInputTextarea
                value={businessContext}
                onChange={(e) => setBusinessContext(e.target.value)}
                placeholder="Explain how this table is used in the business..."
                rows={4}
              />
              <Text level="body-small" className="text-content-layout-3 mt-1">
                Explain the business purpose and common use cases for this table.
              </Text>
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
