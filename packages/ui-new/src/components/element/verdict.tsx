import { cn } from '@rs/tailwind-base'
import type { ReactNode } from 'react'
import { Text } from './text'

export interface VerdictProps {
  /** The verdict itself, in the first tone and medium weight. */
  lead: string
  /** The rest of the sentence, in the second tone. */
  children?: ReactNode
  className?: string
}

/** A 20px sentence that opens a report or an analysis: the verdict, then why. */
export const Verdict = ({ lead, children, className }: VerdictProps) => (
  <Text
    as="p"
    level="headline-3"
    className={cn('text-content-layout-1', className)}
  >
    {lead}
    {children && (
      <span className="font-normal text-content-layout-2"> {children}</span>
    )}
  </Text>
)
