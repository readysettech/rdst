// The Fix block's place while an analysis runs: a 2px line, the four steps with the
// current one marked, and how long a run takes. Nothing animates.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { type AnalyzingStep, progressFraction } from './analyzingSteps'

export function AnalyzingBlock({
  steps,
  aboutSeconds,
  onCancel,
}: {
  steps: AnalyzingStep[]
  /** The usual run length, when known. */
  aboutSeconds: number | null
  /** Stops the analysis; the row returns to its previous status. */
  onCancel?: () => void
}) {
  return (
    <div data-testid="analyzing-block">
      <Text as="h4" level="caption" className="mb-2 text-content-layout-3">
        Analysis
      </Text>
      <div className="my-1 mb-2.5 h-0.5 overflow-hidden rounded-pill bg-surface-primary-soft-hover">
        <div
          className="h-full bg-content-layout-2"
          style={{ width: `${Math.round(progressFraction(steps) * 100)}%` }}
        />
      </div>
      <ol className="m-0 list-none p-0">
        {steps.map((step) => (
          <li
            key={step.label}
            data-state={step.state}
            className={cn(
              'flex h-8.5 items-center gap-3 text-body-small',
              step.state === 'done' && 'text-content-layout-2',
              step.state === 'now' && 'text-content-layout-1',
              step.state === 'todo' && 'text-content-layout-3'
            )}
          >
            <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center">
              {step.state === 'done' ? (
                <Icon name="tick" label="" size="small" />
              ) : step.state === 'now' ? (
                <span className="h-2 w-2 rounded-full bg-content-layout-1" />
              ) : (
                <span className="h-1.5 w-1.5 rounded-full border border-content-layout-3" />
              )}
            </span>
            {step.label}
          </li>
        ))}
      </ol>
      {aboutSeconds != null && (
        <Text as="p" level="caption" className="mt-2 text-content-layout-3">
          About {Math.round(aboutSeconds)} s
        </Text>
      )}
      {onCancel && (
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Cancel"
          className="mt-2"
          onClick={onCancel}
        />
      )}
    </div>
  )
}
