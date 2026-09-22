import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'

/**
 * A small "i" that shows its definition on hover, focus, or tap. Every Jev
 * label, chip, score, and sort option carries one so the explanation is
 * always one gesture away.
 */
export function InfoTip({ text }: { text: string }) {
  return (
    <TooltipProvider delayDuration={100}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Pressable
            aria-label={text}
            className="ml-1 inline-flex shrink-0 items-center rounded text-content-layout-3 hover:text-content-layout-1"
            onClick={(event) => event.stopPropagation()}
          >
            <Icon name="info" label="" aria-hidden="true" className="h-3 w-3" />
          </Pressable>
        </TooltipTrigger>
        <TooltipContent label={text} className="max-w-72 whitespace-normal" />
      </Tooltip>
    </TooltipProvider>
  )
}
