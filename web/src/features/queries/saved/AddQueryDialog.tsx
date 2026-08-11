import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Modal, ModalContentContainer } from '@rs/ui-new/modal'
import { HStack, VStack } from '@rs/ui-new/stack'
import { TabItemButton, TabList } from '@rs/ui-new/tab'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useId } from 'react'
import { TaskDialogContent } from '../../../components/dialog/TaskDialogContent'
import { PathPicker } from '../../../components/PathPicker'
import { SQLInput } from '../../../components/SQLInput'
import type { ImportQueriesResponse } from '../../../lib/api'

export type AddQueryMode = 'add' | 'import'

interface AddQueryDialogProps {
  open: boolean
  mode: AddQueryMode
  onModeChange: (mode: AddQueryMode) => void
  onClose: () => void
  target?: string | null

  sql: string
  onSqlChange: (sql: string) => void
  onSaveQuery: () => void
  saving: boolean

  importPath: string
  onImportPathChange: (path: string) => void
  importUpdate: boolean
  onImportUpdateChange: (update: boolean) => void
  onImport: () => void
  importing: boolean
  importResult?: ImportQueriesResponse
}

export function AddQueryDialog({
  open,
  mode,
  onModeChange,
  onClose,
  target,
  sql,
  onSqlChange,
  onSaveQuery,
  saving,
  importPath,
  onImportPathChange,
  importUpdate,
  onImportUpdateChange,
  onImport,
  importing,
  importResult,
}: AddQueryDialogProps) {
  const id = useId()
  const layoutPrefix = `${id}-add-query-dialog-tabs`
  const panelId = `${id}-panel`

  return (
    <Modal open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <ModalContentContainer open={open}>
        <TaskDialogContent
          size="extra-large"
          icon="folder-file"
          title="Add query"
          description="Save SQL to analyze, test, and cache later."
          bodyClassName="p-0"
          footer={
            <HStack className="justify-end gap-2">
              <Button
                variant="primary"
                modifier="ghost"
                label={mode === 'import' && importResult ? 'Close' : 'Cancel'}
                onClick={onClose}
              />
              {mode === 'add' ? (
                <Button
                  variant="rising"
                  modifier="solid"
                  label="Save query"
                  icon="tick"
                  iconPosition="left"
                  onClick={onSaveQuery}
                  loading={saving}
                  disabled={!sql.trim()}
                />
              ) : (
                <Button
                  variant="rising"
                  modifier="solid"
                  label="Import queries"
                  icon="folder-file"
                  iconPosition="left"
                  onClick={onImport}
                  loading={importing}
                  disabled={!importPath.trim() || importing}
                />
              )}
            </HStack>
          }
        >
          <div className="px-6 pt-2">
            <TabList aria-label="Add query method">
              <TabItemButton
                id={`${id}-tab-add`}
                aria-controls={panelId}
                layoutPrefix={layoutPrefix}
                label="Add query"
                leftIcon="add"
                active={mode === 'add'}
                onClick={() => onModeChange('add')}
              />
              <TabItemButton
                id={`${id}-tab-import`}
                aria-controls={panelId}
                layoutPrefix={layoutPrefix}
                label="Import from file"
                leftIcon="folder-file"
                active={mode === 'import'}
                onClick={() => onModeChange('import')}
              />
            </TabList>
          </div>

          <div
            role="tabpanel"
            id={panelId}
            aria-labelledby={`${id}-tab-${mode}`}
            className="min-h-80 max-h-[65dvh] overflow-y-auto p-6"
          >
            {mode === 'add' ? (
              <SQLInput
                value={sql}
                onChange={onSqlChange}
                placeholder="Enter your SQL query..."
                minHeight="12rem"
                target={target}
                showPrettify
              />
            ) : (
              <VStack className="gap-4 items-stretch">
                <Text level="body-small" className="text-content-layout-3">
                  Choose a .sql file with semicolon-separated queries. Optional{' '}
                  <span className="font-mono">-- name:</span> and{' '}
                  <span className="font-mono">-- target:</span> comments keep
                  each query organized.
                </Text>
                <HStack className="gap-3 items-end flex-wrap">
                  <PathPicker
                    value={importPath}
                    onChange={onImportPathChange}
                    fileExt="sql"
                    label="File path"
                    disabled={importing}
                  />
                  <Button
                    variant="primary"
                    modifier={importUpdate ? 'solid' : 'ghost'}
                    label="Update existing queries"
                    aria-pressed={importUpdate}
                    onClick={() => onImportUpdateChange(!importUpdate)}
                  />
                </HStack>

                {importResult ? (
                  <VStack className="gap-2 items-stretch bg-surface-layout-2/50 rounded-lg p-4 border border-border-layout-1">
                    <HStack className="gap-2 items-center flex-wrap">
                      <Icon
                        name={importResult.success ? 'tick-double' : 'alert'}
                        label=""
                        aria-hidden="true"
                        className={
                          importResult.success
                            ? 'w-4 h-4 text-content-positive-soft'
                            : 'w-4 h-4 text-content-negative-soft'
                        }
                      />
                      <Tag
                        size="small"
                        variant="positive"
                        modifier="ghost"
                        label={`${importResult.imported ?? 0} imported`}
                      />
                      <Tag
                        size="small"
                        variant="primary"
                        modifier="ghost"
                        label={`${importResult.updated ?? 0} updated`}
                      />
                      <Tag
                        size="small"
                        variant="warning"
                        modifier="ghost"
                        label={`${importResult.skipped ?? 0} skipped`}
                      />
                      <Tag
                        size="small"
                        variant="negative"
                        modifier="ghost"
                        label={`${importResult.errors?.length ?? 0} errors`}
                      />
                    </HStack>
                    {(importResult.errors ?? []).map((message, index) => (
                      <HStack
                        key={`import-err-${index}`}
                        className="gap-2 items-center"
                      >
                        <Icon
                          name="alert"
                          label=""
                          aria-hidden="true"
                          className="w-3.5 h-3.5 text-content-negative-soft shrink-0"
                        />
                        <Text
                          level="caption"
                          className="text-content-negative-soft"
                        >
                          {message}
                        </Text>
                      </HStack>
                    ))}
                  </VStack>
                ) : null}
              </VStack>
            )}
          </div>
        </TaskDialogContent>
      </ModalContentContainer>
    </Modal>
  )
}
