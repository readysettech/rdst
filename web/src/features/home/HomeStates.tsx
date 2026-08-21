import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { For } from '@rs/ui-new/for'
import { InteractiveRow } from '@rs/ui-new/interactive-row'
import { Show } from '@rs/ui-new/show'
import { Skeleton } from '@rs/ui-new/skeleton'
import { VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import { HandRaiser } from '../../components/HandRaiser'
import { fillCapturedParams } from '../../lib/sqlParameters'
import { DemoCard, JobCard, PortfolioTile, SetupCard } from './HomeCards'
import type { ContinueItem, PortfolioCounts } from './homeModel'
import type { AiKeyState } from './useHomeController'

const CONTINUE_TAG: Record<
  ContinueItem['kind'],
  { label: string; variant: 'primary' | 'informative' | 'positive' }
> = {
  asked: { label: 'asked', variant: 'primary' },
  analyzed: { label: 'analyzed', variant: 'informative' },
  compared: { label: 'compared', variant: 'positive' },
  saved: { label: 'saved', variant: 'informative' },
}

export function FirstRunHome({ aiKeyState }: { aiKeyState: AiKeyState }) {
  const navigate = useNavigate()
  const keyLabel =
    aiKeyState === 'ready'
      ? 'Key configured'
      : aiKeyState === 'loading'
        ? 'Checking setup'
        : 'Configure AI'

  return (
    <VStack className="items-stretch gap-5">
      <DemoCard />
      <div className="grid grid-cols-1 gap-4 tablet:grid-cols-2">
        <SetupCard
          number="01"
          icon="database"
          title="Connect your database"
          description="Add PostgreSQL or MySQL with a read-only user. Your application traffic is never modified."
          action={
            <Button
              variant="primary"
              modifier="solid"
              label="Connect a database"
              icon="arrow-right"
              iconPosition="right"
              onClick={() => navigate({ to: '/onboarding' })}
            />
          }
        />
        <SetupCard
          number="02"
          icon="sparkles"
          title="Configure AI"
          description="Enable schema descriptions, Ask, and analysis guidance. You can use your own key or trial credits."
          action={
            <Button
              variant="primary"
              modifier={aiKeyState === 'ready' ? 'ghost' : 'outline'}
              label={keyLabel}
              disabled={aiKeyState === 'loading'}
              onClick={() => navigate({ to: '/configure' })}
            />
          }
        />
      </div>
    </VStack>
  )
}

export function ConnectedHome({
  target,
  aiKeyState,
}: {
  target: string
  aiKeyState: AiKeyState
}) {
  const navigate = useNavigate()
  const canDiscover = aiKeyState === 'ready'

  return (
    <VStack className="items-stretch gap-4">
      <div className="grid grid-cols-1 gap-4 desktop:grid-cols-12">
        <Card className="desktop:col-span-7">
          <Card.Header>
            <Card.Title>Build the semantic layer for {target}</Card.Title>
            <Card.Description>
              Profile tables and columns so Ask and analysis can reason about
              this database instead of guessing.
            </Card.Description>
          </Card.Header>
          <Card.Content className="grid gap-3 tablet:grid-cols-3">
            {[
              'Table purpose and business context',
              'Column shapes, ranges, and null rates',
              'Sharper questions and safer generated SQL',
            ].map((label) => (
              <div
                key={label}
                className="rounded-xl bg-surface-layout-2/50 p-4"
              >
                <Text level="body-small" className="text-content-layout-2">
                  {label}
                </Text>
              </div>
            ))}
          </Card.Content>
          <Card.Footer className="justify-between">
            <Text level="caption" className="text-content-layout-3">
              Read-only · runs in the background
            </Text>
            <Button
              variant="primary"
              modifier={canDiscover ? 'solid' : 'outline'}
              label={canDiscover ? 'Discover schema' : 'Configure AI first'}
              icon="arrow-right"
              iconPosition="right"
              onClick={() =>
                navigate({ to: canDiscover ? '/schema' : '/configure' })
              }
            />
          </Card.Footer>
        </Card>
        <div className="desktop:col-span-5">
          <DemoCard compact />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 tablet:grid-cols-2">
        <JobCard
          to="/audit"
          icon="document-validation"
          title="Run a health check"
          description="Find sizing risks, slow spots, and cache opportunities while discovery runs."
        />
        <JobCard
          to="/ask"
          icon="sparkles"
          title="Ask with live introspection"
          description="Start now; answers become richer when the semantic layer is ready."
        />
      </div>
    </VStack>
  )
}

export function ActiveHome({
  target,
  counts,
  recents,
  registryState,
  auditState,
  lastAuditLabel,
  retentionDays,
  retryRegistry,
}: {
  target: string
  counts: PortfolioCounts
  recents: ContinueItem[]
  registryState: 'loading' | 'error' | 'ready'
  auditState: 'loading' | 'error' | 'ready'
  lastAuditLabel: string | null
  retentionDays: number | null
  retryRegistry: () => void
}) {
  const navigate = useNavigate()
  const sustainedUse =
    retentionDays !== null && retentionDays >= 30 && counts.compared > 0
  const openPrimaryAction = () => {
    if (counts.candidates > 0) {
      navigate({ to: '/cache', search: { view: 'compare' } })
      return
    }
    navigate({ to: '/queries', search: { view: 'all' } })
  }

  return (
    <VStack className="items-stretch gap-4">
      {/* Leads with recall, not the portfolio tiles (C2 / plan §1: task 3 for
        both personas is finding an existing result). */}
      <Show when={registryState === 'ready' && recents.length > 0}>
        <Card>
          <Card.Header>
            <Card.Title>Continue where you left off</Card.Title>
            <Card.Description>
              Resume the exact query and target context from your latest work.
            </Card.Description>
          </Card.Header>
          <Card.Content className="space-y-2">
            <For each={recents} keyExtractor={(item) => item.hash}>
              {(item) => (
                <InteractiveRow
                  label={`Continue with ${item.nextAction.toLowerCase()} for ${item.label}`}
                  onClick={() => {
                    if (item.nextAction === 'Analyze') {
                      navigate({
                        to: '/results',
                        search: {
                          query: fillCapturedParams(
                            item.sql,
                            item.mostRecentParams
                          ),
                          target,
                          origin: 'home',
                        },
                      })
                      return
                    }
                    if (item.nextAction === 'Review') {
                      // The analysis this query already has, reopened in the
                      // Query Library's drawer. Recall never re-runs (A6).
                      navigate({
                        to: '/queries',
                        search: { analyze: item.hash },
                      })
                      return
                    }
                    navigate({
                      to: '/cache',
                      search: { view: 'compare', hash: item.hash },
                    })
                  }}
                  className="flex items-center gap-3 rounded-xl bg-surface-layout-2/50 px-3 py-3 text-left transition-colors hover:bg-surface-raised"
                >
                  <Tag
                    size="small"
                    variant={CONTINUE_TAG[item.kind].variant}
                    modifier="ghost"
                    label={CONTINUE_TAG[item.kind].label}
                  />
                  <Text
                    level="mono-small"
                    className="min-w-0 flex-1 truncate text-content-layout-2"
                  >
                    {item.label}
                  </Text>
                  <Text level="caption" className="text-content-primary-soft">
                    {item.nextAction} →
                  </Text>
                </InteractiveRow>
              )}
            </For>
          </Card.Content>
        </Card>
      </Show>

      <div className="grid grid-cols-1 gap-4 desktop:grid-cols-12">
        <Show when={registryState === 'loading'}>
          <Card className="desktop:col-span-7">
            <Card.Header>
              <Skeleton className="h-5 w-52 rounded-lg" />
              <Skeleton className="h-4 w-80 max-w-full rounded-lg" />
            </Card.Header>
            <Card.Content className="grid grid-cols-2 gap-3 tablet:grid-cols-4">
              {Array.from({ length: 4 }, (_, index) => (
                <Skeleton key={index} className="h-24 rounded-xl" />
              ))}
            </Card.Content>
          </Card>
        </Show>

        <Show when={registryState === 'error'}>
          <Card className="desktop:col-span-7">
            <Card.Header>
              <Card.Title>Query activity could not be loaded</Card.Title>
              <Card.Description>
                Home could not read the Query Library summary. Your saved
                queries and database were not changed.
              </Card.Description>
            </Card.Header>
            <Card.Footer className="justify-start gap-2">
              <Button
                variant="primary"
                modifier="solid"
                label="Try again"
                onClick={retryRegistry}
              />
              <Button
                variant="primary"
                modifier="ghost"
                label="Open Query Library"
                onClick={() => navigate({ to: '/queries' })}
              />
            </Card.Footer>
          </Card>
        </Show>

        <Show when={registryState === 'ready'}>
          <Card className="desktop:col-span-7">
            <Card.Header>
              <Card.Title>
                {counts.candidates > 0
                  ? `${counts.candidates} Readyset candidate${counts.candidates === 1 ? '' : 's'} to measure`
                  : 'Your query workload is ready to revisit'}
              </Card.Title>
              <Card.Description>
                {counts.candidates > 0
                  ? 'Compare the highest-impact candidates against the upstream database under the same load.'
                  : 'Review new workload evidence, analysis results, and saved questions in one library.'}
              </Card.Description>
            </Card.Header>
            <Card.Content className="grid grid-cols-2 gap-3 tablet:grid-cols-4">
              <PortfolioTile
                label="Asked"
                value={counts.asked}
                unit="saved questions"
                to="/queries?source=ask"
              />
              <PortfolioTile
                label="Analyzed"
                value={counts.analyzed}
                unit="with analysis"
                to="/queries?view=needs-analysis"
              />
              <PortfolioTile
                label="Compared"
                value={counts.compared}
                unit="measured runs"
                to="/cache?view=compare"
              />
              <PortfolioTile
                label="Candidates"
                value={counts.candidates}
                unit="ready to measure"
                to="/queries?view=ready-to-cache"
                highlight={counts.candidates > 0}
              />
            </Card.Content>
            <Card.Footer>
              <Button
                variant="primary"
                modifier={counts.candidates > 0 ? 'solid' : 'outline'}
                label={
                  counts.candidates > 0 ? 'Compare candidates' : 'Open Queries'
                }
                icon="arrow-right"
                iconPosition="right"
                onClick={openPrimaryAction}
              />
            </Card.Footer>
          </Card>
        </Show>

        <div className="desktop:col-span-5">
          <DemoCard compact />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 tablet:grid-cols-2">
        <JobCard
          to="/audit"
          icon="document-validation"
          title="Health check"
          description="Validate sizing, slow spots, and workload risks."
          chip={
            auditState === 'error'
              ? { label: 'Status unavailable', variant: 'warning' }
              : auditState === 'loading'
                ? { label: 'Checking history', variant: 'neutral' }
                : lastAuditLabel
                  ? {
                      label: `Last run ${lastAuditLabel}`,
                      variant: 'informative',
                    }
                  : { label: 'Never run', variant: 'neutral' }
          }
        />
        <JobCard
          to="/queries"
          icon="observe"
          title="Query Library"
          description="Review every observed, saved, and analyzed query for this target."
          chip={{ label: target, variant: 'informative' }}
        />
      </div>

      <Show when={sustainedUse}>
        <HandRaiser
          signal="retention_30d"
          tone="accent"
          showDismiss
          message={`A month of RDST on ${target}, with ${counts.compared} Readyset comparison${counts.compared === 1 ? '' : 's'} retained. If this is heading to production, we'd like to help you size it.`}
        />
      </Show>
    </VStack>
  )
}
