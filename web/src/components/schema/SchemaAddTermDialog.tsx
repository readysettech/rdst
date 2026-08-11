import { BaseInputText } from '@rs/ui-new/base-input-text'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Modal, ModalContentContainer } from '@rs/ui-new/modal'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import type { AddTerminologyData, SchemaTerminology } from '../../types/schema'
import { TaskDialogContent } from '../dialog/TaskDialogContent'

interface SchemaAddTermDialogProps {
  isOpen: boolean
  editingTerm?: SchemaTerminology | null
  onClose: () => void
  onSave: (data: AddTerminologyData) => Promise<boolean>
  isLoading?: boolean
}

export function SchemaAddTermDialog({
  isOpen,
  editingTerm,
  onClose,
  onSave,
  isLoading,
}: SchemaAddTermDialogProps) {
  const [term, setTerm] = useState('')
  const [definition, setDefinition] = useState('')
  const [sqlPattern, setSqlPattern] = useState('')
  const [synonymsText, setSynonymsText] = useState('')

  const isEditing = !!editingTerm

  // Populate form when editing
  useEffect(() => {
    if (editingTerm) {
      setTerm(editingTerm.term)
      setDefinition(editingTerm.definition)
      setSqlPattern(editingTerm.sql_pattern)
      setSynonymsText(editingTerm.synonyms.join(', '))
    } else {
      setTerm('')
      setDefinition('')
      setSqlPattern('')
      setSynonymsText('')
    }
  }, [editingTerm, isOpen])

  const handleSave = async () => {
    if (!term.trim() || !definition.trim() || !sqlPattern.trim()) {
      return
    }

    const synonyms = synonymsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)

    const success = await onSave({
      term: term.trim(),
      definition: definition.trim(),
      sql_pattern: sqlPattern.trim(),
      synonyms: synonyms.length > 0 ? synonyms : undefined,
    })

    if (success) {
      // Reset form
      setTerm('')
      setDefinition('')
      setSqlPattern('')
      setSynonymsText('')
      onClose()
    }
  }

  const isValid = term.trim() && definition.trim() && sqlPattern.trim()

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <TaskDialogContent
          size="base"
          icon="edit"
          title={isEditing ? 'Edit business term' : 'Add business term'}
          description="Connect familiar language to the SQL that implements it."
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
                label={isEditing ? 'Save changes' : 'Add term'}
                onClick={handleSave}
                loading={isLoading}
                disabled={isLoading || !isValid}
              />
            </div>
          }
        >
          {/* Term */}
          <div>
            <Text level="label-small" className="text-content-layout-2 mb-1">
              Term <span className="text-content-negative-soft">*</span>
            </Text>
            <BaseInputText
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder="e.g., active users"
              disabled={isEditing}
            />
            <Text level="body-small" className="text-content-layout-3 mt-1">
              {isEditing
                ? 'Term name cannot be changed.'
                : 'The business term or phrase.'}
            </Text>
          </div>

          {/* Definition */}
          <div>
            <Text level="label-small" className="text-content-layout-2 mb-1">
              Definition <span className="text-content-negative-soft">*</span>
            </Text>
            <BaseInputTextarea
              value={definition}
              onChange={(e) => setDefinition(e.target.value)}
              placeholder="e.g., Users who have logged in within the last 30 days"
              rows={2}
            />
            <Text level="body-small" className="text-content-layout-3 mt-1">
              Plain English definition of what this term means.
            </Text>
          </div>

          {/* SQL Pattern */}
          <div>
            <Text level="label-small" className="text-content-layout-2 mb-1">
              SQL Pattern <span className="text-content-negative-soft">*</span>
            </Text>
            <BaseInputTextarea
              value={sqlPattern}
              onChange={(e) => setSqlPattern(e.target.value)}
              placeholder="e.g., last_login_at > NOW() - INTERVAL '30 days'"
              rows={2}
              className="font-mono"
            />
            <Text level="body-small" className="text-content-layout-3 mt-1">
              SQL condition or expression that implements this term.
            </Text>
          </div>

          {/* Synonyms */}
          <div>
            <Text level="label-small" className="text-content-layout-2 mb-1">
              Synonyms
            </Text>
            <BaseInputText
              value={synonymsText}
              onChange={(e) => setSynonymsText(e.target.value)}
              placeholder="e.g., engaged users, recent users"
            />
            <Text level="body-small" className="text-content-layout-3 mt-1">
              Comma-separated list of alternative names for this term.
            </Text>
          </div>
        </TaskDialogContent>
      </ModalContentContainer>
    </Modal>
  )
}
