import { InlineNotice } from '@rs/ui-new/error-state'
import { m } from '@rs/ui-new/motion'
import { ParameterDialog } from '../../../components/top'
import { AddQueryDialog } from '../saved/AddQueryDialog'
import { QueryLibraryList } from './QueryLibraryList'
import { QueryLibraryToolbar } from './QueryLibraryToolbar'
import type { QueryLibraryController } from './useQueryLibraryController'

export function QueryLibraryPage({
  controller,
}: {
  controller: QueryLibraryController
}) {
  const { registry, addDialog } = controller

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
        {controller.library.discovery.capture_warning ? (
          <InlineNotice
            errorClass="database"
            accent="warning"
            icon="alert"
            title="Historical query discovery needs setup"
            message={`${controller.library.discovery.capture_warning.source} is unavailable, so Readyset may miss queries that are not active during a live sample. ${controller.library.discovery.capture_warning.instructions.join(' ')}`}
            trustworthy="Saved queries and completed Jev assessments remain available."
          />
        ) : null}
        <QueryLibraryToolbar controller={controller} />
        <QueryLibraryList controller={controller} />
      </m.div>

      {controller.paramDialog ? (
        <ParameterDialog
          isOpen
          query={controller.paramDialog.sql}
          target={controller.target}
          queryHash={controller.paramDialog.hash}
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
      ) : null}
    </div>
  )
}
