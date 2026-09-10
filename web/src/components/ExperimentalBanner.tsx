import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

// A warning-toned notice marking a feature as experimental / not yet ready for
// production. Shown at the top of Code scan and Guards, the two surfaces the
// sidebar deliberately leaves out, so the reader learns why the page they are
// on is not in the nav. [E-15, F-05]
export function ExperimentalBanner({ name }: { name?: string }) {
  return (
    <div className="rounded-xl border border-border-warning-soft bg-surface-warning-soft p-4">
      <HStack className="gap-3 items-start">
        <Icon
          name="alert"
          label=""
          className="w-5 h-5 text-content-warning-soft shrink-0 mt-0.5"
        />
        <VStack className="gap-0.5 items-start min-w-0">
          <Text level="label-small" className="text-content-warning-soft">
            Experimental
          </Text>
          <Text level="body-small" className="text-content-warning-soft">
            {name ?? 'This feature'} is experimental and not yet ready for
            production use, so it is kept out of the sidebar. Behavior and
            results may change.
          </Text>
        </VStack>
      </HStack>
    </div>
  )
}
