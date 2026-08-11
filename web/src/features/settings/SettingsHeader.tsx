import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

export function SettingsHeader() {
  return (
    <m.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <HStack className="gap-4 items-center">
        <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
          <Icon
            name="settings"
            label="Configure"
            className="w-6 h-6 text-content-primary-soft"
          />
        </div>
        <VStack className="gap-1 items-start">
          <Text as="h1" level="headline-3" className="text-content-layout-1">
            Settings
          </Text>
          <Text level="body-small" className="text-content-layout-3">
            Manage connections, AI access, and local data.
          </Text>
        </VStack>
      </HStack>
    </m.div>
  )
}
