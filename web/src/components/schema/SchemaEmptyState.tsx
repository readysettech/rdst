import { Text } from '@rs/ui-new/text'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Card } from '@rs/ui-new/card'
import { HStack, VStack } from '@rs/ui-new/stack'
import { m } from '@rs/ui-new/motion'

interface SchemaEmptyStateProps {
  target: string
  onInit: () => void
  isLoading?: boolean
}

export function SchemaEmptyState({ target, onInit, isLoading }: SchemaEmptyStateProps) {
  return (
    <Card className="w-full">
      <Card.Content className="py-8">
        <VStack className="gap-8 items-center">
          {/* Header */}
          <VStack className="gap-3 items-center text-center">
            <m.div
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 0.3 }}
              className="w-16 h-16 rounded-2xl bg-gradient-to-br from-violet-500/20 to-purple-600/20 flex items-center justify-center border border-violet-500/30"
            >
              <Icon name="sparkles" label="Initialize" className="w-8 h-8 text-violet-400" />
            </m.div>
            <VStack className="gap-1 items-center">
              <Text level="headline-4" className="text-content-layout-1">
                Initialize Semantic Layer
              </Text>
              <Text level="body-medium" className="text-content-layout-3 max-w-md">
                Create a semantic layer for <strong className="text-content-layout-1">{target}</strong> to
                enable AI-powered natural language queries.
              </Text>
            </VStack>
          </VStack>

          {/* Feature cards */}
          <div className="grid gap-4 sm:grid-cols-2 w-full max-w-xl">
            <m.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.1 }}
              className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-5"
            >
              <HStack className="gap-3 items-start">
                <div className="w-10 h-10 rounded-lg bg-surface-primary-soft/20 flex items-center justify-center shrink-0">
                  <Icon name="database" label="Introspection" className="w-5 h-5 text-content-primary-soft" />
                </div>
                <VStack className="gap-1 items-start">
                  <Text level="label-medium" className="text-content-layout-1">
                    Schema Introspection
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    Automatically discovers tables, columns, and relationships from your database.
                  </Text>
                </VStack>
              </HStack>
            </m.div>
            <m.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.2 }}
              className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-5"
            >
              <HStack className="gap-3 items-start">
                <div className="w-10 h-10 rounded-lg bg-surface-positive-soft/20 flex items-center justify-center shrink-0">
                  <Icon name="folder-file" label="Configuration" className="w-5 h-5 text-content-positive-soft" />
                </div>
                <VStack className="gap-1 items-start">
                  <Text level="label-medium" className="text-content-layout-1">
                    YAML Configuration
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    A customizable file with table descriptions, column metadata, and business terminology.
                  </Text>
                </VStack>
              </HStack>
            </m.div>
          </div>

          {/* Action */}
          <m.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.3 }}
            className="flex flex-col items-center gap-3"
          >
            <Button
              variant="rising"
              modifier="solid"
              icon="sparkles"
              iconPosition="left"
              label={isLoading ? 'Initializing...' : 'Initialize Schema'}
              onClick={onInit}
              loading={isLoading}
              disabled={isLoading}
            />
            <Text level="caption" className="text-content-layout-3">
              You can customize the semantic layer after initialization
            </Text>
          </m.div>
        </VStack>
      </Card.Content>
    </Card>
  )
}
