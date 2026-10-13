import { Slot } from '@radix-ui/react-slot'
import { cn } from '@rs/tailwind-base'
import {
  type ComponentProps,
  type CSSProperties,
  createContext,
  type ReactNode,
  useContext,
} from 'react'
import { controlTransition, focusRingClass } from '../../helpers/focus'

const ColumnsContext = createContext<string | undefined>(undefined)

export interface DividedListColumn {
  label: string
  align?: 'start' | 'end'
  /** For a column that drops out at some widths, with the rows' cells and `columns`. */
  className?: string
}

export interface DividedListProps {
  /** A CSS grid-template-columns value shared by every row. */
  columns?: string
  /** Column labels above the first hairline. */
  head?: DividedListColumn[]
  children: ReactNode
  className?: string
}

/** Rows separated by 1px hairlines, on the same grid, with no boxes around them. */
export const DividedList = ({
  columns,
  head,
  children,
  className,
}: DividedListProps) => (
  <ColumnsContext.Provider value={columns}>
    <div className={className}>
      {head && (
        <div
          className="grid items-center gap-4 pb-2 text-label-extra-small font-normal text-content-layout-3"
          style={{ gridTemplateColumns: columns }}
        >
          {head.map((column) => (
            <span
              key={column.label || `${head.indexOf(column)}`}
              className={cn(
                column.align === 'end' && 'text-right',
                column.className
              )}
            >
              {column.label}
            </span>
          ))}
        </div>
      )}
      <div className="border-t border-border-layout-2">{children}</div>
    </div>
  </ColumnsContext.Provider>
)

export interface ListRowProps extends Omit<ComponentProps<'div'>, 'children'> {
  /** The row is a link or a button: it lights on hover and takes focus. */
  interactive?: boolean
  /**
   * The row is the one open on the page: raised one tone, with its detail
   * under the cells.
   */
  open?: boolean
  /** What an open row shows under its cells. */
  detail?: ReactNode
  /** Render the caller's element (a router link) as the row itself. */
  asChild?: boolean
  /** Grid columns for this row alone; the list's columns otherwise. */
  columns?: string
  children: ReactNode
}

/**
 * One row of a DividedList: a grid on the list's columns, 52px tall, with the
 * hairline under it. An open row is the only raised element in the list.
 */
export const ListRow = ({
  interactive = false,
  open = false,
  detail,
  asChild = false,
  columns,
  className,
  style,
  children,
  ...props
}: ListRowProps) => {
  const listColumns = useContext(ColumnsContext)
  const Comp = asChild ? Slot : 'div'
  const gridStyle: CSSProperties = {
    ...style,
    gridTemplateColumns: columns ?? listColumns,
  }
  const cells = (
    <Comp
      data-open={open || undefined}
      className={cn(
        'grid min-h-13 items-center gap-4 py-2.5',
        !open && 'border-b border-border-layout-2',
        interactive && [
          'cursor-pointer hover:bg-row-hover',
          controlTransition,
          'duration-fast ease-base',
          focusRingClass,
        ],
        className
      )}
      style={gridStyle}
      {...props}
    >
      {children}
    </Comp>
  )
  if (!open) return cells
  return (
    <div className="-mx-3 rounded-panel border border-border-layout-2 bg-surface-primary-soft px-3">
      {cells}
      {detail && <div className="pb-4">{detail}</div>}
    </div>
  )
}
