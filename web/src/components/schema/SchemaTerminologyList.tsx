import { Text } from '@rs/ui-new/text'
import { Tag } from '@rs/ui-new/tag'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Show } from '@rs/ui-new/show'
import { m } from '@rs/ui-new/motion'
import type { SchemaTerminology } from '../../types/schema'

interface SchemaTerminologyListProps {
  terminology: SchemaTerminology[]
  onEdit?: (term: SchemaTerminology) => void
}

export function SchemaTerminologyList({ terminology, onEdit }: SchemaTerminologyListProps) {
  if (terminology.length === 0) {
    return (
      <div className="px-6 py-12">
        <VStack className="gap-3 items-center">
          <div className="w-12 h-12 rounded-xl bg-surface-layout-2 flex items-center justify-center">
            <Icon name="folder-file" label="No terms" className="w-6 h-6 text-content-layout-3" />
          </div>
          <VStack className="gap-1 items-center">
            <Text level="body-medium" className="text-content-layout-3">
              No business terminology defined
            </Text>
            <Text level="caption" className="text-content-layout-3">
              Add terms to help the AI understand your domain language
            </Text>
          </VStack>
        </VStack>
      </div>
    )
  }

  return (
    <div className="divide-y divide-border-layout-1">
      {terminology.map((term, index) => (
        <m.div
          key={term.term}
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.2, delay: index * 0.03 }}
          className="px-5 py-4 hover:bg-surface-layout-2/30 transition-colors"
        >
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-lg bg-surface-primary-soft/20 flex items-center justify-center shrink-0">
              <Icon name="folder-file" label="Term" className="w-5 h-5 text-content-primary-soft" />
            </div>
            <div className="flex-1 min-w-0">
              <Text level="label-medium" className="text-content-layout-1">
                {term.term}
              </Text>
              <Text level="body-small" className="text-content-layout-2 mt-1">
                {term.definition}
              </Text>
              <div className="mt-3 flex flex-col gap-2">
                <HStack className="gap-2 items-start flex-wrap">
                  <Text level="caption" className="text-content-layout-3 shrink-0 pt-0.5">
                    SQL:
                  </Text>
                  <code className="text-xs bg-surface-layout-2 px-2 py-1 rounded font-mono text-content-layout-1 break-all">
                    {term.sql_pattern}
                  </code>
                </HStack>
                <Show when={term.synonyms.length > 0}>
                  <HStack className="gap-2 items-center flex-wrap">
                    <Text level="caption" className="text-content-layout-3 shrink-0">
                      Synonyms:
                    </Text>
                    {term.synonyms.map((syn) => (
                      <Tag key={syn} size="small" label={syn} modifier="outline" />
                    ))}
                  </HStack>
                </Show>
              </div>
            </div>
            <Show when={!!onEdit}>
              <Button
                modifier="ghost"
                size="small"
                icon="edit"
                iconPosition="icon"
                label="Edit"
                onClick={() => onEdit?.(term)}
              />
            </Show>
          </div>
        </m.div>
      ))}
    </div>
  )
}
