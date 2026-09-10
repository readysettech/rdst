/**
 * Header for the Scan page
 */

import { IconTile } from '@rs/ui-new/icon-tile'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import type { ScanState } from '../../types/scan'

interface ScanHeaderProps {
  state: ScanState
  queriesCount: number
}

export function ScanHeader({ state, queriesCount }: ScanHeaderProps) {
  return (
    <m.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      {/* The hero anatomy every other route carries: tile, title, one line of
          description. [E-15] */}
      <HStack className="gap-4 items-start min-w-0">
        <IconTile icon="search" />
        <VStack className="gap-1 items-start min-w-0">
          <HStack className="gap-3 items-center">
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              Code scan
            </Text>
            {state === 'complete' && queriesCount > 0 && (
              <Tag
                size="small"
                variant="positive"
                modifier="ghost"
                label={`${queriesCount} quer${queriesCount === 1 ? 'y' : 'ies'} found`}
              />
            )}
          </HStack>
          <Text level="body-small" className="text-content-layout-3">
            Scan your codebase for ORM queries, extract SQL, and analyze
            performance.
          </Text>
        </VStack>
      </HStack>
    </m.div>
  )
}
