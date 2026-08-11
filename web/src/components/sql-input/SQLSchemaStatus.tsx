import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { SQLSchemaState } from './useSQLSchema'

function SQLSchemaStatusContent({ schema, isLoading }: SQLSchemaState) {
  if (isLoading) {
    return (
      <m.div
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -5 }}
        className="flex items-center gap-2"
      >
        <Spinner size="base" color="primary-soft" />
        <Text level="caption" className="text-content-layout-3">
          Connecting to database...
        </Text>
      </m.div>
    )
  }

  if (schema) {
    const tableCount = Object.keys(schema.tables).length

    return (
      <m.div
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -5 }}
        className="flex items-center gap-3"
      >
        <HStack className="gap-1.5 items-center">
          <Icon
            name="database"
            label="Tables"
            className="w-3 h-3 text-content-layout-3"
          />
          <Text level="caption" className="text-content-layout-3">
            {tableCount} table{tableCount !== 1 ? 's' : ''}
          </Text>
        </HStack>
        {schema.dialect && (
          <>
            <div className="w-px h-3 bg-border-layout-1" />
            <Text
              level="caption"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              {schema.dialect}
            </Text>
          </>
        )}
      </m.div>
    )
  }

  return (
    <m.div
      initial={{ opacity: 0, y: 5 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -5 }}
      className="flex items-center gap-1.5"
    >
      <div className="w-2 h-2 rounded-full bg-content-layout-3" />
      <Text level="caption" className="text-content-layout-3">
        No target selected
      </Text>
    </m.div>
  )
}

export function SQLSchemaStatus(state: SQLSchemaState) {
  return (
    <AnimatePresence mode="wait">
      <SQLSchemaStatusContent
        key={
          state.isLoading
            ? 'loading'
            : state.schema
              ? 'connected'
              : 'disconnected'
        }
        {...state}
      />
    </AnimatePresence>
  )
}
