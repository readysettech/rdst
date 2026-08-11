import { Button } from '@rs/ui-new/button'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { SlowQueriesPage } from '../slow/SlowQueriesPage'

export function QueryLibraryLiveCapture({ onClose }: { onClose: () => void }) {
  return (
    <VStack className="items-stretch gap-6 w-full">
      <HStack className="items-center justify-between gap-4 flex-wrap">
        <VStack className="items-start gap-1">
          <Text as="h2" level="headline-4" className="text-content-layout-1">
            Live capture
          </Text>
          <Text level="body-small" className="text-content-layout-3">
            Watch active database traffic and add newly observed queries to the
            library.
          </Text>
        </VStack>
        <Button
          variant="primary"
          modifier="ghost"
          icon="arrow-left"
          iconPosition="left"
          label="Back to queries"
          onClick={onClose}
        />
      </HStack>

      <SlowQueriesPage initialMode="realtime" realtimeOnly />
    </VStack>
  )
}
