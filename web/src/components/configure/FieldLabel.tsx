import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

/**
 * A form label programmatically associated with its input via `htmlFor` — the
 * flagship-setup a11y fix (configure had 8 labels, 0 associated). `Text` does
 * not type `htmlFor`, so the association lives on a native `<label>` wrapping a
 * `Text` span. [USE-088]
 */
export function FieldLabel({
  htmlFor,
  children,
}: {
  htmlFor: string
  children: ReactNode
}) {
  return (
    <label htmlFor={htmlFor} className="block mb-1.5">
      <Text as="span" level="label-small" className="text-content-layout-2">
        {children}
      </Text>
    </label>
  )
}
