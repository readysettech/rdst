import { Button } from '@rs/ui-new/button'
import { HStack } from '@rs/ui-new/stack'
import { lazy, Suspense } from 'react'
import { WorkspaceLayout } from '../../../components/workspace/WorkspaceLayout'
import { AnalyzeDrawer } from '../analyze-drawer/AnalyzeDrawer'
import { QueryLibraryDiscoveryStatus } from '../library/QueryLibraryDiscoveryStatus'
import type {
  QueryLibrarySearch,
  QueryLibraryView,
} from '../library/queryLibraryState'
import { useQueryLibraryController } from '../library/useQueryLibraryController'
import { QueriesPaneSkeleton } from './QueriesPaneSkeleton'

const PANEL_ID = 'queries-panel'

const QueryLibraryPage = lazy(() =>
  import('../library/QueryLibraryPage').then((module) => ({
    default: module.QueryLibraryPage,
  }))
)

export function QueriesWorkspace({ search }: { search: QueryLibrarySearch }) {
  const controller = useQueryLibraryController({ search })

  return (
    <WorkspaceLayout<QueryLibraryView>
      title="Queries"
      description="Find, save, and improve the queries that shape your database workload."
      icon="folder-file"
      panelId={PANEL_ID}
      headerDivider
      titleMeta={<QueryLibraryDiscoveryStatus controller={controller} />}
      headerActions={
        <HStack className="items-center gap-2 ml-auto">
          <Button
            variant="primary"
            modifier="ghost"
            label="Run benchmark"
            icon="play"
            iconPosition="left"
            onClick={controller.navigation.openBenchmark}
          />
          <Button
            variant="primary"
            modifier="solid"
            label="Add query"
            icon="add"
            iconPosition="left"
            onClick={controller.addDialog.openDialog}
          />
        </HStack>
      }
    >
      <Suspense fallback={<QueriesPaneSkeleton />}>
        <QueryLibraryPage controller={controller} />
      </Suspense>
      <AnalyzeDrawer
        link={controller.analyzeDrawer.link}
        loaded={controller.analyzeDrawer.entries}
        librarySearch={controller.analyzeDrawer.librarySearch}
        target={controller.target}
        onClose={controller.analyzeDrawer.close}
        onOpenLink={controller.analyzeDrawer.open}
        onToggleStar={controller.rowActions.toggleStar}
      />
    </WorkspaceLayout>
  )
}
