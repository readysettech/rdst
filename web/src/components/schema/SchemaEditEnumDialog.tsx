import { useState, useEffect } from 'react'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Text } from '@rs/ui-new/text'
import { Button } from '@rs/ui-new/button'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalTitle,
} from '@rs/ui-new/modal'
import type { SchemaTableColumn, AddEnumData } from '../../types/schema'

interface SchemaEditEnumDialogProps {
  isOpen: boolean
  tableName: string
  column: SchemaTableColumn | null
  onClose: () => void
  onSave: (data: AddEnumData) => Promise<boolean>
  isLoading?: boolean
}

interface EnumEntry {
  value: string
  meaning: string
}

export function SchemaEditEnumDialog({
  isOpen,
  tableName,
  column,
  onClose,
  onSave,
  isLoading,
}: SchemaEditEnumDialogProps) {
  const [entries, setEntries] = useState<EnumEntry[]>([])
  const [newValue, setNewValue] = useState('')
  const [newMeaning, setNewMeaning] = useState('')

  useEffect(() => {
    if (column?.enum_values) {
      const existingEntries = Object.entries(column.enum_values).map(([value, meaning]) => ({
        value,
        meaning,
      }))
      setEntries(existingEntries)
    } else {
      setEntries([])
    }
    setNewValue('')
    setNewMeaning('')
  }, [column])

  if (!column) return null

  const handleAddEntry = () => {
    if (newValue.trim()) {
      setEntries([...entries, { value: newValue.trim(), meaning: newMeaning.trim() }])
      setNewValue('')
      setNewMeaning('')
    }
  }

  const handleRemoveEntry = (index: number) => {
    setEntries(entries.filter((_, i) => i !== index))
  }

  const handleUpdateEntry = (index: number, field: 'value' | 'meaning', value: string) => {
    const updated = [...entries]
    updated[index][field] = value
    setEntries(updated)
  }

  const handleSave = async () => {
    const enumValues: Record<string, string> = {}
    for (const entry of entries) {
      if (entry.value.trim()) {
        enumValues[entry.value.trim()] = entry.meaning.trim()
      }
    }

    const success = await onSave({
      table_name: tableName,
      column_name: column.name,
      enum_values: enumValues,
    })
    if (success) {
      onClose()
    }
  }

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="large" className="gap-6">
          <ModalTitle>Edit Enum Values</ModalTitle>

          <div className="space-y-4">
            {/* Column info */}
            <div className="bg-surface-layout-1 rounded-lg p-3">
              <Text level="label-small" className="text-content-layout-2">
                Column
              </Text>
              <code className="text-sm font-mono text-content-layout-1 mt-1 block">
                {tableName}.{column.name}
              </code>
            </div>

            {/* Existing entries */}
            {entries.length > 0 && (
              <div className="space-y-2">
                <Text level="label-small" className="text-content-layout-2">
                  Values ({entries.length})
                </Text>
                <Scrollable className="max-h-64">
                  <div className="space-y-2">
                    {entries.map((entry, index) => (
                      <div
                        key={index}
                        className="flex items-start gap-2 bg-surface-layout-1 rounded-lg p-2"
                      >
                        <div className="flex-1 min-w-0">
                          <BaseInputText
                            value={entry.value}
                            onChange={(e) => handleUpdateEntry(index, 'value', e.target.value)}
                            placeholder="Value"
                            className="mb-1"
                          />
                          <BaseInputText
                            value={entry.meaning}
                            onChange={(e) => handleUpdateEntry(index, 'meaning', e.target.value)}
                            placeholder="Description/meaning"
                          />
                        </div>
                        <Button
                          modifier="ghost"
                          size="small"
                          label="Remove"
                          onClick={() => handleRemoveEntry(index)}
                        />
                      </div>
                    ))}
                  </div>
                </Scrollable>
              </div>
            )}

            {/* Add new entry */}
            <div className="border-t border-border-layout-1 pt-4">
              <Text level="label-small" className="text-content-layout-2 mb-2">
                Add New Value
              </Text>
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <BaseInputText
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    placeholder="Value (e.g., active, pending)"
                  />
                </div>
                <div className="flex-1">
                  <BaseInputText
                    value={newMeaning}
                    onChange={(e) => setNewMeaning(e.target.value)}
                    placeholder="Meaning (e.g., User is currently active)"
                  />
                </div>
                <Button
                  modifier="outline"
                  size="small"
                  label="Add"
                  onClick={handleAddEntry}
                  disabled={!newValue.trim()}
                />
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
