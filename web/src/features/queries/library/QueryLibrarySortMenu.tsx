import { Button } from '@rs/ui-new/button'
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownSeparator,
  DropdownSub,
  DropdownSubContent,
  DropdownSubTrigger,
  DropdownTrigger,
} from '@rs/ui-new/dropdown-rf'
import { Icon } from '@rs/ui-new/icon'
import { HStack } from '@rs/ui-new/stack'
import { InfoTip } from '../saved/InfoTip'
import {
  QUERY_LIBRARY_FINDING_DEFINITIONS,
  QUERY_LIBRARY_JEV_SORTS,
  QUERY_LIBRARY_SORT_LABELS,
  QUERY_LIBRARY_STANDARD_SORTS,
} from './queryLibrarySelectors'
import type { QueryLibrarySort } from './queryLibraryState'

// DropdownSubTrigger is the unstyled Radix primitive; give it the vocabulary
// the styled DropdownItem uses so the submenu row reads as a peer of the
// options above it. Mirrors SchemaManageMenu's export submenu.
const subTriggerClass = [
  'relative flex h-10 min-w-60 items-center justify-between gap-2 px-4 py-1',
  'rounded-xl text-label-large text-content-layout-2 cursor-pointer select-none outline-none',
  'transition-colors duration-fast ease-base',
  'hover:bg-surface-primary-soft-hover hover:text-content-primary-soft',
  'focus:bg-surface-primary-soft-hover focus:text-content-primary-soft',
  'data-[state=open]:bg-surface-primary-soft-hover data-[state=open]:text-content-primary-soft',
].join(' ')

/**
 * Sort picker. The Jev orderings live behind one submenu so the flat list
 * stays the set of workload metrics it has always been, and a concern sort
 * reads as "order by this concern" rather than as another top-level metric.
 */
export function QueryLibrarySortMenu({
  id,
  sort,
  onSortChange,
}: {
  id: string
  sort: QueryLibrarySort
  onSortChange: (value: QueryLibrarySort) => void
}) {
  const jevActive = sort.startsWith('jev-')

  return (
    <Dropdown>
      <DropdownTrigger asChild>
        <Button
          id={id}
          variant="primary"
          modifier="outline"
          size="base"
          label=""
          aria-label="Order queries"
          className="w-full justify-between"
          data-testid="query-library-sort-trigger"
          innerClassName="gap-2"
        >
          <span className="truncate">
            {jevActive ? 'Jev · ' : ''}
            {QUERY_LIBRARY_SORT_LABELS[sort]}
          </span>
          <Icon
            name="chevron-down"
            label=""
            aria-hidden="true"
            className="h-3.5 w-3.5 shrink-0"
          />
        </Button>
      </DropdownTrigger>
      <DropdownContent align="start" className="min-w-60">
        {QUERY_LIBRARY_STANDARD_SORTS.map((value) => (
          <DropdownItem
            key={value}
            active={value === sort}
            onSelect={() => onSortChange(value)}
          >
            {QUERY_LIBRARY_SORT_LABELS[value]}
          </DropdownItem>
        ))}
        <DropdownSeparator />
        <DropdownSub>
          <DropdownSubTrigger
            className={subTriggerClass}
            data-active={jevActive || undefined}
          >
            <HStack className="items-center gap-2">
              <Icon
                name="sparkles"
                label=""
                aria-hidden="true"
                className="h-4 w-4"
              />
              <span>Jev</span>
            </HStack>
            <Icon
              name="chevron-right"
              label=""
              aria-hidden="true"
              className="h-3.5 w-3.5"
            />
          </DropdownSubTrigger>
          <DropdownSubContent className="min-w-60">
            {QUERY_LIBRARY_JEV_SORTS.map((value) => (
              <DropdownItem
                key={value}
                active={value === sort}
                onSelect={() => onSortChange(value)}
              >
                <HStack className="w-full items-center justify-between gap-2">
                  <span>{QUERY_LIBRARY_SORT_LABELS[value]}</span>
                  <InfoTip
                    text={
                      QUERY_LIBRARY_FINDING_DEFINITIONS[
                        value.replace('jev-', '')
                      ]
                    }
                  />
                </HStack>
              </DropdownItem>
            ))}
          </DropdownSubContent>
        </DropdownSub>
      </DropdownContent>
    </Dropdown>
  )
}
