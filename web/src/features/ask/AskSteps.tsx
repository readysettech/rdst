// The answer area while a question runs: three quiet step lines, the usual wait and a
// ghost Cancel.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { STEPS, stepForPhase } from './askModel'

export function AskSteps({
  phase,
  onCancel,
}: {
  phase: string | undefined
  onCancel: () => void
}) {
  const at = stepForPhase(phase)
  return (
    <div data-testid="ask-steps" className="mt-9">
      <ol
        aria-label="Answer steps"
        className="m-0 flex list-none flex-col gap-2 p-0 text-body-small text-content-layout-3"
      >
        {STEPS.map((label, index) => {
          const state = index < at ? 'done' : index === at ? 'now' : 'todo'
          return (
            <li
              key={label}
              data-state={state}
              className={cn(
                'flex items-center gap-2.5',
                state === 'now' && 'font-medium text-content-layout-1'
              )}
            >
              {state === 'done' ? (
                <Icon name="tick" label="" size="small" />
              ) : (
                <span
                  className={cn(
                    'h-3.5 w-3.5 shrink-0 rounded-full border border-border-layout-2',
                    state === 'now' && 'border-4 border-content-layout-1'
                  )}
                />
              )}
              {label}
            </li>
          )
        })}
      </ol>
      <div className="mt-4 flex items-center gap-3">
        <Text as="span" level="caption" className="text-content-layout-3">
          Usually under 5 s
        </Text>
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Cancel"
          onClick={onCancel}
        />
      </div>
    </div>
  )
}
