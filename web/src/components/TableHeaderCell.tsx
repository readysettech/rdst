/**
 * The one table-header standard. Before this, column headers drifted across four
 * conventions — `text-xs … content-layout-3 uppercase` (audit/benchmark/cache/
 * fleet), the non-uppercase `content-layout-2 label-small` data grids (Ask,
 * agents), and Demo's `font-semibold` mixed-case outlier — which read as the
 * owner's "some table-header fonts are too big / inconsistent" finding.
 *
 * This component is the single token usage every `<th>` consumes so the type
 * scale (`label-small`), weight (`font-medium` — no faux-bold), colour
 * (`content-layout-3`), casing (uppercase + `tracking-wider`) and padding
 * (`px-4 py-3`) are defined once and cannot drift again.
 * [triage §2.2 Genel 1; VIS-008/010 systematise, VIS-037/038 type scale,
 *  VIS-013/014 two weights / no faux-bold, USE-097 consistency; C-07/C-08]
 *
 * Promotion path: app-local for now (co-located with the app's tables). When a
 * shared table primitive lands in `@rs/ui-new`, fold `TABLE_HEADER_CLASS` into it
 * verbatim.
 */

import type { ComponentProps, ReactNode } from 'react';

type Align = 'left' | 'right' | 'center';

/** The canonical table-header cell classes (sans alignment). */
const TABLE_HEADER_CLASS =
  'px-4 py-3 text-label-small font-medium text-content-layout-3 uppercase tracking-wider';

const ALIGN_CLASS: Record<Align, string> = {
  left: 'text-left',
  right: 'text-right',
  center: 'text-center',
};

interface TableHeaderCellProps extends Omit<ComponentProps<'th'>, 'children'> {
  /** Column text alignment (default `left`; use `right` for numeric columns). */
  align?: Align;
  /** Extra classes for width / sticky / etc. (e.g. `w-24`). */
  className?: string;
  children?: ReactNode;
}

export function TableHeaderCell({
  align = 'left',
  className,
  children,
  ...rest
}: TableHeaderCellProps) {
  return (
    <th
      className={`${TABLE_HEADER_CLASS} ${ALIGN_CLASS[align]}${className ? ` ${className}` : ''}`}
      {...rest}
    >
      {children}
    </th>
  );
}
