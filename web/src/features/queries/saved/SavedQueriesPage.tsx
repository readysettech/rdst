import { m } from '@rs/ui-new/motion'
import { ParameterDialog } from '../../../components/top'
import { AddQueryDialog } from './AddQueryDialog'
import { SavedQueriesControlCard } from './SavedQueriesControlCard'
import { SavedQueryList } from './SavedQueryList'
import { useSavedQueriesController } from './useSavedQueriesController'

export function SavedQueriesPage({
  deepLinkHash,
  deepLinkRunId,
}: {
  deepLinkHash?: string
  deepLinkRunId?: string
}) {
  const controller = useSavedQueriesController({
    deepLinkHash,
    deepLinkRunId,
  })
  const { registry, selection, pagination, addDialog } = controller

  return (
    <div className="space-y-6 w-full">
      <AddQueryDialog
        open={addDialog.open}
        mode={addDialog.mode}
        onModeChange={addDialog.setMode}
        onClose={addDialog.closeDialog}
        target={controller.target}
        sql={addDialog.sql}
        onSqlChange={addDialog.setSql}
        onSaveQuery={addDialog.save}
        saving={addDialog.saving}
        importPath={addDialog.importPath}
        onImportPathChange={addDialog.setImportPath}
        importUpdate={addDialog.importUpdate}
        onImportUpdateChange={addDialog.setImportUpdate}
        onImport={addDialog.importQueries}
        importing={addDialog.importing}
        importResult={addDialog.importResult}
      />

      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
      >
        <SavedQueriesControlCard
          searchTerm={controller.searchTerm}
          onSearchChange={controller.setSearch}
          sourceFilter={controller.sourceFilter}
          sourceOptions={selection.sourceOptions}
          searchResultCount={selection.searchFiltered.length}
          filteredCount={selection.filteredQueries.length}
          total={registry.total}
          isFetching={registry.isFetching}
          hasPreviousPage={pagination.hasPrevPage}
          hasNextPage={pagination.hasNextPage}
          onSelectSource={controller.selectSource}
          onPreviousPage={registry.prevPage}
          onNextPage={registry.nextPage}
          onOpenBenchmark={controller.navigation.openBenchmark}
          onAddQuery={addDialog.openDialog}
        />

        <SavedQueryList controller={controller} />
      </m.div>

      {controller.paramDialog && (
        <ParameterDialog
          isOpen
          query={controller.paramDialog.sql}
          target={controller.target}
          initialValues={
            registry.queries.find(
              (query) => query.hash === controller.paramDialog?.hash
            )?.most_recent_params
          }
          submitLabel="Run test"
          submitIcon="speedometer"
          onClose={controller.closeParamDialog}
          onSubmit={controller.submitParamDialog}
        />
      )}
    </div>
  )
}
