import { Dropdown } from '@rs/ui-new/dropdown'
import { IconButton } from '@rs/ui-new/icon-button'
import type { TopQuery } from '../../../types/top'

interface SlowQueryMenuProps {
  query: TopQuery
  onCache?: (query: TopQuery) => void
  caching: boolean
  cached: boolean
}

/** Keeps secondary row actions out of the primary action slot. */
export function SlowQueryMenu({
  query,
  onCache,
  caching,
  cached,
}: SlowQueryMenuProps) {
  if (!onCache) return null

  return (
    <Dropdown>
      {/* Dropdown.Trigger requires a single ref-forwarding child. */}
      <Dropdown.Trigger asChild>
        <IconButton
          variant="primary"
          modifier="ghost"
          size="small"
          icon="more"
          label="More actions"
          tooltip={false}
        />
      </Dropdown.Trigger>
      <Dropdown.Content align="end" className="min-w-52">
        <Dropdown.Item
          leftIcon="database-settings"
          label={cached ? 'Cached' : caching ? 'Caching…' : 'Cache query'}
          disabled={cached || caching}
          onClick={() => onCache(query)}
        />
      </Dropdown.Content>
    </Dropdown>
  )
}
