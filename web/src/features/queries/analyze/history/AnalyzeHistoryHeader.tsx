import { Icon } from '@rs/ui-new/icon'
import { HStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'

export function AnalyzeHistoryHeader({ count }: { count: number }) {
  return (
    <HStack className="justify-between items-center gap-4 px-1">
      <HStack className="gap-2 items-center">
        <Icon
          name="folder-file"
          label=""
          aria-hidden="true"
          className="w-4 h-4 text-content-layout-3"
        />
        <h2 id="recent-queries-heading">
          <Text as="span" level="label-small" className="text-content-layout-2">
            Recent queries
          </Text>
        </h2>
        <Tag
          size="small"
          variant="informative"
          modifier="ghost"
          label={`${count}`}
        />
      </HStack>
      <Link
        to="/queries"
        search={{ view: 'saved' }}
        className="text-sm text-content-primary-soft hover:underline whitespace-nowrap"
      >
        View all →
      </Link>
    </HStack>
  )
}
