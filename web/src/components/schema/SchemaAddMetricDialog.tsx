import { useState, useEffect } from 'react'
import { Text } from '@rs/ui-new/text'
import { Button } from '@rs/ui-new/button'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalTitle,
} from '@rs/ui-new/modal'
import type { AddMetricData, SchemaMetric } from '../../types/schema'

interface SchemaAddMetricDialogProps {
  isOpen: boolean
  editingMetric?: SchemaMetric | null
  onClose: () => void
  onSave: (data: AddMetricData) => Promise<boolean>
  isLoading?: boolean
}

export function SchemaAddMetricDialog({
  isOpen,
  editingMetric,
  onClose,
  onSave,
  isLoading,
}: SchemaAddMetricDialogProps) {
  const [name, setName] = useState('')
  const [definition, setDefinition] = useState('')
  const [sql, setSql] = useState('')
  const [unit, setUnit] = useState('')

  const isEditing = !!editingMetric

  // Populate form when editing
  useEffect(() => {
    if (editingMetric) {
      setName(editingMetric.name)
      setDefinition(editingMetric.definition)
      setSql(editingMetric.sql)
      setUnit('')
    } else {
      setName('')
      setDefinition('')
      setSql('')
      setUnit('')
    }
  }, [editingMetric, isOpen])

  const handleSave = async () => {
    if (!name.trim() || !definition.trim() || !sql.trim()) return

    const success = await onSave({
      name: name.trim(),
      definition: definition.trim(),
      sql: sql.trim(),
      unit: unit.trim() || undefined,
    })

    if (success) {
      setName('')
      setDefinition('')
      setSql('')
      setUnit('')
      onClose()
    }
  }

  const isValid = name.trim() && definition.trim() && sql.trim()

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base" className="gap-6">
          <ModalTitle>{isEditing ? 'Edit Metric' : 'Add Metric'}</ModalTitle>

          <div className="space-y-4">
            {/* Name */}
            <div>
              <Text level="label-small" className="text-content-layout-2 mb-1">
                Metric Name <span className="text-red-400">*</span>
              </Text>
              <BaseInputText
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., monthly_active_users"
                disabled={isEditing}
              />
              {isEditing && (
                <Text level="body-small" className="text-content-layout-3 mt-1">
                  Metric name cannot be changed.
                </Text>
              )}
            </div>

            {/* Definition */}
            <div>
              <Text level="label-small" className="text-content-layout-2 mb-1">
                Definition <span className="text-red-400">*</span>
              </Text>
              <BaseInputTextarea
                value={definition}
                onChange={(e) => setDefinition(e.target.value)}
                placeholder="e.g., Count of unique users who logged in within the last 30 days"
                rows={2}
              />
              <Text level="body-small" className="text-content-layout-3 mt-1">
                Human-readable description of what this metric measures.
              </Text>
            </div>

            {/* SQL */}
            <div>
              <Text level="label-small" className="text-content-layout-2 mb-1">
                SQL Expression <span className="text-red-400">*</span>
              </Text>
              <BaseInputTextarea
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                placeholder="e.g., COUNT(DISTINCT user_id) FILTER (WHERE last_login > NOW() - INTERVAL '30 days')"
                rows={3}
                className="font-mono"
              />
              <Text level="body-small" className="text-content-layout-3 mt-1">
                SQL expression that calculates this metric.
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
                placeholder="e.g., users, USD, percent"
              />
              <Text level="body-small" className="text-content-layout-3 mt-1">
                Unit of measurement for this metric.
              </Text>
            </div>
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3">
            <Button modifier="ghost" label="Cancel" onClick={onClose} disabled={isLoading} />
            <Button
              label={isLoading ? 'Saving...' : isEditing ? 'Save Changes' : 'Add Metric'}
              onClick={handleSave}
              loading={isLoading}
              disabled={isLoading || !isValid}
            />
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
