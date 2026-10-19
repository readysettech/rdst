// Ways back into the tour: resume where it was closed, or watch it from the start.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { openTour, useSavedTourStep } from './onboardingState'

export function TourEntry({ className }: { className?: string }) {
  const saved = useSavedTourStep()
  return (
    <div className={cn('flex flex-wrap items-center gap-x-2', className)}>
      {saved != null && (
        <Button
          variant="primary"
          modifier="link"
          size="small"
          icon="play"
          iconPosition="left"
          label={`Resume the tour at step ${saved + 1}`}
          onClick={() => openTour(saved)}
          data-testid="tour-resume"
        />
      )}
      <Button
        variant="primary"
        modifier="link"
        size="small"
        icon={saved == null ? 'play' : undefined}
        iconPosition={saved == null ? 'left' : 'none'}
        label={saved == null ? 'See how it works' : 'Start it over'}
        onClick={() => openTour()}
        data-testid="tour-replay"
      />
    </div>
  )
}
