import { Button } from '@rs/ui-new/button'
import { Show } from '@rs/ui-new/show'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { AnimatedSurfaceBackdrop } from '../../../components/AnimatedSurfaceBackdrop'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'

interface SavedQueriesHeroProps {
  hero: QueryRegistryEntry | null
  heroWhy: string
  heroMoreCount: number
  caching: boolean
  showTelemetryNudge: boolean
  onCache: (hash: string, sql: string) => void
  onOpenSlowQueries: () => void
}

export function SavedQueriesHero({
  hero,
  heroWhy,
  heroMoreCount,
  caching,
  showTelemetryNudge,
  onCache,
  onOpenSlowQueries,
}: SavedQueriesHeroProps) {
  return (
    <>
      <Show when={!!hero}>
        <div className="relative overflow-hidden rounded-2xl bg-surface-rising-solid px-6 py-5 shadow-elevation-2">
          <AnimatedSurfaceBackdrop palette="purple" />

          <Text
            level="overline"
            className="relative text-content-rising-solid/80 uppercase tracking-wider"
          >
            Your biggest win
          </Text>
          <div className="relative mt-4 flex items-center gap-4 flex-wrap">
            <VStack className="gap-1 items-start flex-1 min-w-0">
              <div
                title={hero?.sql}
                className="text-body-medium font-mono text-content-rising-solid truncate w-full"
              >
                {hero?.sql}
              </div>
              <Text
                level="body-small"
                className="text-content-primary-solid bg-surface-primary-solid/90 px-1"
              >
                {heroWhy}
              </Text>
            </VStack>
            <Button
              variant="primary"
              modifier="solid"
              icon="database-settings"
              iconPosition="left"
              label="Compare & test"
              className="bg-surface-layout-1 text-content-layout-1 hover:bg-surface-layout-2 active:bg-surface-layout-2"
              loading={caching}
              onClick={() => hero && onCache(hero.hash, hero.sql)}
            />
          </div>
          <Show when={heroMoreCount > 0}>
            <Text
              level="caption"
              className="relative text-content-rising-solid/80 mt-4"
            >
              {heroMoreCount} more worth caching below
            </Text>
          </Show>
        </div>
      </Show>

      <Show when={showTelemetryNudge}>
        <Button
          variant="primary"
          modifier="outline"
          icon="observe"
          iconPosition="left"
          label="Run Slow Queries to surface your biggest caching wins"
          className="w-full justify-start"
          onClick={onOpenSlowQueries}
        />
      </Show>
    </>
  )
}
