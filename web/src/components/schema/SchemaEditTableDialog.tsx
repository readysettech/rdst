import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Modal, ModalContentContainer } from '@rs/ui-new/modal'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import type { AddTableData, SchemaTable } from '../../types/schema'
import { TaskDialogContent } from '../dialog/TaskDialogContent'

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
        <TaskDialogContent
          size="base"
          icon="database"
          title="Edit table"
          description={`Describe how ${table.name} is used in your application.`}
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
                label="Save table"
                onClick={handleSave}
                loading={isLoading}
                disabled={isLoading}
              />
            </div>
          }
        >
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
        </TaskDialogContent>
      </ModalContentContainer>
    </Modal>
  )
}
