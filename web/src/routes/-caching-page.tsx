// Caching workspace component — moved out of the route config into this
// route-ignored sibling (TanStack skips `-`-prefixed files) so the code-splitter
// can relocate the CodeMirror SQL-editor stack (pulled in transitively by the
// embedded Compare/Load test panes) out of the eager entry chunk. The route file
// imports `CachingPage` only for its `component:` wrapper. See top.tsx §Defect D-1.

import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { useNavigate } from '@tanstack/react-router'
import { WorkspaceLayout } from '../components/workspace/WorkspaceLayout'
import { ComparePage } from '../features/caching/compare/ComparePage'
import { BenchmarkPage } from './-benchmark-page'
import type { PerformanceTestView } from './cache'

const TEST_VIEWS: Array<{
  value: PerformanceTestView
  label: string
  icon: IconStrokeName
}> = [
  { value: 'compare', label: 'Compare', icon: 'play' },
  { value: 'load-test', label: 'Load test', icon: 'layers' },
]

const PANEL_ID = 'caching-panel'

export function CachingPage({
  view,
  deepLinkHash,
  selectedRunId,
}: {
  view: PerformanceTestView
  deepLinkHash?: string
  selectedRunId?: string
}) {
  const navigate = useNavigate()

  return (
    <WorkspaceLayout
      title="Performance tests"
      description="Measure Readyset impact or validate database capacity."
      icon="speedometer"
      views={TEST_VIEWS}
      activeView={view}
      layoutPrefix="performance-tests"
      panelId={PANEL_ID}
      tabsLabel="Performance test modes"
      fullBleedTabs
      fillViewport
      onViewChange={(nextView) =>
        navigate({
          to: '/cache',
          search: { view: nextView },
          replace: false,
        })
      }
    >
      {view === 'compare' && (
        <ComparePage
          initialQueryHash={deepLinkHash}
          onOpenQueries={() =>
            navigate({
              to: '/queries',
              search: deepLinkHash ? { hash: deepLinkHash } : {},
              replace: false,
            })
          }
          onFindQueries={() =>
            navigate({
              to: '/queries',
              search: { view: 'high-impact' },
              replace: false,
            })
          }
        />
      )}
      {view === 'load-test' && (
        <BenchmarkPage
          selectedRunId={selectedRunId}
          onClearSelectedRun={() =>
            navigate({
              to: '/cache',
              search: { view: 'load-test' },
              replace: true,
            })
          }
        />
      )}
    </WorkspaceLayout>
  )
}
