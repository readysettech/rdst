import { m } from '@rs/ui-new/motion'
import { AnalyzeEditor } from './AnalyzeEditor'
import { AnalyzeHistory } from './history/AnalyzeHistory'
import { LocalCompatibilityLab } from './LocalCompatibilityLab'
import { useAnalyzeController } from './useAnalyzeController'

export function AnalyzePage() {
  const controller = useAnalyzeController()
  return (
    <div className="space-y-8 w-full">
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
      >
        <AnalyzeEditor controller={controller} />
      </m.div>

      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <LocalCompatibilityLab
          target={controller.target.name}
          query={controller.editor.value}
          disabled={controller.target.lock.isLocked}
        />
      </m.div>

      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.3 }}
      >
        <AnalyzeHistory
          queries={controller.history.queries}
          onSelect={controller.actions.selectHistory}
          isLoading={controller.history.isLoading}
          error={controller.history.error}
          onRetry={controller.history.retry}
        />
      </m.div>
    </div>
  )
}
