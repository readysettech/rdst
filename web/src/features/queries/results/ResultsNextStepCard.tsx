import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { SqlTokens } from '../../../components/SqlTokens'
import { ResultSectionIndex } from './ResultSectionIndex'
import { resultToneStyles } from './resultStyles'
import type { ResultNextStep } from './resultsSelectors'

function getRationaleSummary(body: string) {
  const match = body.match(/^(.+?[.!?])(?:\s+|$)([\s\S]+)$/)
  return match?.[1] ?? body
}

function NextStepEvidencePanel({
  step,
}: {
  step: Extract<ResultNextStep, { kind: 'readyset' | 'none' }>
}) {
  const isReadyset = step.kind === 'readyset'

  return (
    <div className="mt-auto flex h-36 flex-col overflow-hidden rounded-xl border border-border-layout-1 bg-surface-layout-2/40">
      <div className="flex items-center justify-between gap-4 border-b border-border-layout-1 px-4 py-3">
        <Text level="label-extra-small" className="text-content-layout-3">
          {isReadyset ? 'Verified action' : 'Recommendation status'}
        </Text>
        <Text
          level="label-extra-small"
          className={
            isReadyset ? 'text-content-positive-soft' : 'text-content-info-soft'
          }
        >
          {isReadyset ? 'Ready to continue' : 'Measured'}
        </Text>
      </div>

      <HStack className="min-h-0 flex-1 items-center gap-3 px-4 py-3">
        <IconTile
          icon={isReadyset ? 'database-settings' : 'info'}
          size="base"
          accent={isReadyset ? 'positive' : 'info'}
        />
        <VStack className="min-w-0 items-start gap-1">
          <Text level="label-small" className="text-content-layout-1">
            {isReadyset ? 'Ready for cache setup' : 'No confident change'}
          </Text>
          <Text level="caption" className="line-clamp-2 text-content-layout-3">
            {isReadyset
              ? step.supportingText
              : 'No tested rewrite, index, or cache action was strong enough to recommend.'}
          </Text>
        </VStack>
      </HStack>
    </div>
  )
}

export function ResultsNextStepCard({
  step,
  onSetUpCaching,
  isCaching,
}: {
  step: ResultNextStep
  onSetUpCaching?: () => void
  isCaching?: boolean
}) {
  const style = resultToneStyles[step.tone]
  const rationaleSummary = getRationaleSummary(step.body)
  const sqlLabel =
    step.kind === 'rewrite' ? 'Tested rewrite SQL' : 'Suggested index SQL'
  const hasSql = 'sql' in step
  const footerLabel =
    step.kind === 'index'
      ? 'Review before applying'
      : step.kind === 'rewrite'
        ? step.supportingText
        : step.kind === 'readyset'
          ? onSetUpCaching
            ? undefined
            : 'Readyset compatibility verified'
          : 'No high-confidence action'
  const footerIcon =
    step.kind === 'index'
      ? ('alert' as const)
      : step.kind === 'none'
        ? ('info' as const)
        : ('tick-double' as const)

  return (
    <Card className="h-full">
      <Card.Header className="items-stretch gap-3 tablet:flex-row tablet:items-center tablet:justify-between">
        <HStack className="items-center gap-3">
          <ResultSectionIndex value={2} />
          <VStack className="items-start gap-1">
            <Card.Title>Recommended next step</Card.Title>
            <Card.Description>
              Best-supported action from this analysis.
            </Card.Description>
          </VStack>
        </HStack>
        <div className="self-end tablet:self-auto">
          <Tag variant={style.tag} modifier="ghost" label={step.evidence} />
        </div>
      </Card.Header>

      <Card.Content className="flex flex-1 flex-col gap-8 p-6">
        <HStack className="items-start gap-5">
          <VStack className="min-w-0 items-start gap-2">
            <Text as="h2" level="subtitle-1" className="text-content-layout-1">
              {step.title}
            </Text>
            <Text
              level="body-small"
              className="max-w-3xl leading-relaxed text-content-layout-2"
            >
              {rationaleSummary}
            </Text>
          </VStack>
        </HStack>

        {hasSql ? (
          <div className="mt-auto flex h-36 flex-col overflow-hidden rounded-xl border border-border-layout-1 bg-surface-layout-2/40">
            <div className="border-b border-border-layout-1 px-4 py-3">
              <Text level="label-extra-small" className="text-content-layout-3">
                {sqlLabel}
              </Text>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-4">
              <SqlTokens sql={step.sql} title={step.sql} />
            </div>
          </div>
        ) : (
          <NextStepEvidencePanel step={step} />
        )}
      </Card.Content>

      <Card.Footer
        className={
          footerLabel
            ? 'min-h-18 justify-between gap-4 flex-wrap'
            : 'min-h-18 justify-end gap-4 flex-wrap'
        }
      >
        {footerLabel ? (
          <HStack className="min-w-0 items-center gap-1.5">
            <Icon
              name={footerIcon}
              label=""
              aria-hidden="true"
              className={`h-3.5 w-3.5 shrink-0 ${
                step.kind === 'index' ? 'text-content-warning-soft' : style.text
              }`}
            />
            <Text
              level="caption"
              className={
                step.kind === 'index' ? 'text-content-layout-3' : style.text
              }
            >
              {footerLabel}
            </Text>
          </HStack>
        ) : null}

        {hasSql ? (
          <CopyButton text={step.sql} label="Copy SQL" />
        ) : step.kind === 'readyset' && onSetUpCaching ? (
          <Button
            variant="rising"
            modifier="solid"
            label="Set up caching…"
            icon="database-settings"
            iconPosition="left"
            onClick={onSetUpCaching}
            loading={isCaching}
          />
        ) : null}
      </Card.Footer>
    </Card>
  )
}
