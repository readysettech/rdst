/**
 * One labelled zone of the Settings page.
 *
 * One active Settings panel. The page-level tabs already provide the primary
 * information architecture, so each panel uses a compact horizontal identity
 * row and gives the actual controls the full content width.
 * [VIS-113, VIS-029, VIS-030, VIS-036, VIS-104, USE-041, USE-006]
 */

import { cn } from '@rs/tailwind-base'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

interface SettingsSectionProps {
  /** Section heading for the active Settings panel. */
  title: string
  /** One-line description under the heading. */
  description: string
  /** Section-level action rendered opposite the heading. */
  action?: ReactNode
  /** Anchor id for deep-links / scroll targets (e.g. "ai", "dev"). */
  id?: string
  /** Extra classes on the <section> (page-level spacing / dividers live here). */
  className?: string
  children: ReactNode
}

export function SettingsSection({
  title,
  description,
  action,
  id,
  className,
  children,
}: SettingsSectionProps) {
  return (
    <section id={id} className={cn('space-y-5 scroll-mt-6', className)}>
      <HStack className="items-start justify-between gap-4 flex-wrap">
        <VStack className="gap-1 items-start min-w-0">
          <Text as="h2" level="headline-4" className="text-content-layout-1">
            {title}
          </Text>
          <Text level="body-small" className="text-content-layout-3">
            {description}
          </Text>
        </VStack>
        {action}
      </HStack>

      <div className="min-w-0">{children}</div>
    </section>
  )
}
