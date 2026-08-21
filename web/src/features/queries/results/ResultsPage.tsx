import { Button } from '@rs/ui-new/button'
import { m } from '@rs/ui-new/motion'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { trackEvent } from '../../../lib/analytics'
import { ResultsBody } from './ResultsBody'
import type { ResultsSearch } from './types'
import { useResultsController } from './useResultsController'

export function ResultsPage({ search }: { search: ResultsSearch }) {
  const controller = useResultsController(search)
  const { origin, backLabel, actions } = controller

  const handleBack = () => {
    trackEvent('back_to_origin', { origin })
    actions.goBack()
  }

  return (
    <div className="w-full space-y-6">
      <m.header
        className="flex flex-col items-start justify-between gap-4 tablet:flex-row tablet:items-end"
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
      >
        <VStack className="items-start gap-2">
          <Button
            variant="primary"
            modifier="link"
            size="small"
            icon="arrow-left"
            iconPosition="left"
            label={backLabel}
            onClick={handleBack}
            className="no-underline"
          />
          <VStack className="items-start gap-1">
            <Text as="h1" level="headline-2" className="text-content-layout-1">
              Query analysis
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              Measured performance, practical improvements, and Readyset fit.
            </Text>
          </VStack>
        </VStack>
      </m.header>

      <ResultsBody controller={controller} />
    </div>
  )
}
