/**
 * One labelled zone of the Settings page.
 *
 * Two-column "sectioned settings" layout modelled on the owner's reference
 * (`owner-manual/refs/CleanShot 2026-07-20 at 22.25.19.png`): the section
 * heading + one-line description (and any section action) sit in a narrow left
 * column, the section content in a wider right column — a 2:3 split (cols 2 + 3
 * of a 5-col grid) from tablet width up. Below tablet the two columns stack into
 * a single column (mobile-first), so the identity block sits above the content.
 * Each section is a nameable area a user can point at; sections are separated by
 * space + a hairline rule at the page level, not by heavy boxes.
 * [VIS-113, VIS-029, VIS-030, VIS-036, VIS-104, USE-041, USE-006]
 */

import { cn } from '@rs/tailwind-base'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

interface SettingsSectionProps {
  /** Section heading (names the zone — matches nothing the user clicked to get here). */
  title: string
  /** One-line description under the heading. */
  description: string
  /** Section action (e.g. an Add button); rendered under the description in the left column. */
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
    <section
      id={id}
      className={cn(
        // 2:3 split from tablet up (cols 2 + 3 of 5); single column below,
        // where the left identity block stacks above the content.
        'grid grid-cols-1 gap-y-4 scroll-mt-6',
        'tablet:grid-cols-5 tablet:gap-x-10 tablet:gap-y-0',
        className
      )}
    >
      {/* Left column — section identity: heading, description, optional action. */}
      <VStack className="gap-1 items-start min-w-0 tablet:col-span-2">
        <Text as="h2" level="headline-4" className="text-content-layout-1">
          {title}
        </Text>
        <Text level="body-small" className="text-content-layout-3">
          {description}
        </Text>
        {action ? <div className="pt-3">{action}</div> : null}
      </VStack>

      {/* Right column — the section content. */}
      <div className="min-w-0 tablet:col-span-3">{children}</div>
    </section>
  )
}
