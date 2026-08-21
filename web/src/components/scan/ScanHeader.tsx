/**
 * Header for the Scan page
 */

import { Icon } from '@rs/ui-new/icon'
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
      <HStack className="gap-4 items-center">
        <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-rising-soft flex items-center justify-center">
          <Icon
            name="search"
            label="Code scan"
            className="w-6 h-6 text-content-primary-soft"
          />
        </div>
        <VStack className="gap-1 items-start">
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
