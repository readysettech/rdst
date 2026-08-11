import { TargetLockNotice } from '../../../components/TargetLockNotice'
import { AnalyzeQueryEditor } from './AnalyzeQueryEditor'
import type { AnalyzeController } from './useAnalyzeController'

export function AnalyzeEditor({
  controller,
}: {
  controller: AnalyzeController
}) {
  const { editor, target, actions } = controller

  return (
    <section aria-label="SQL query">
      {target.lock.isLocked && (
        <div className="mb-4">
          <TargetLockNotice
            message={target.lock.message}
            requirements={target.lock.missingTargetRequirements}
            keyringAvailable={target.lock.keyringAvailable}
          />
        </div>
      )}

      <div
        ref={editor.ref}
        data-testid="analyze-editor"
        className={`rounded-2xl transition-shadow duration-500 ${
          editor.isHighlighted
            ? 'ring-2 ring-border-primary-soft ring-offset-2 ring-offset-surface-layout-1'
            : 'ring-0 ring-transparent'
        }`}
      >
        <AnalyzeQueryEditor
          value={editor.value}
          onChange={editor.setValue}
          onAnalyze={actions.analyze}
          disabled={editor.disabled}
          target={target.name}
          fast={editor.fast}
          onFastChange={editor.setFast}
        />
      </div>
    </section>
  )
}
