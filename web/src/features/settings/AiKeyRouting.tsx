// What each key covers, and the account's AI monitoring limit, under AI access.
import { Text } from '@rs/ui-new/text'
import {
  AllowanceNotice,
  MonitoringLimitLine,
} from '@/features/monitor-kit/AiMonitoringStatus'
import { useMonitoringLimit } from '@/lib/monitor/hooks'

export type AiProvider = 'readyset' | 'claude' | null

/** One line per kind of AI work, saying which key covers it. */
export function keyRoutingLines(
  provider: AiProvider,
  signedIn: boolean
): string[] {
  const jev = signedIn
    ? 'Quick checks and monitoring alerts always run through Readyset and use your Readyset allowance.'
    : 'Quick checks and monitoring alerts always run through Readyset, so they need you signed in.'
  if (provider === 'claude')
    return [
      'Your Anthropic key covers Analyze, Ask, deep analysis and nightly checkups.',
      jev,
    ]
  if (provider === 'readyset')
    return [
      'Your Readyset account covers Analyze, Ask, deep analysis, nightly checkups, quick checks and monitoring alerts.',
      'With your own Anthropic key, it covers Analyze, Ask, deep analysis and reports instead, and you can use AI monitoring on more databases.',
    ]
  return [
    'Sign in to Readyset or add your own Anthropic key to use AI.',
    'Quick checks and monitoring alerts always run through Readyset.',
  ]
}

export function AiKeyRouting({
  provider,
  signedIn,
}: {
  provider: AiProvider
  signedIn: boolean
}) {
  const limit = useMonitoringLimit()
  return (
    <div
      className="space-y-3 rounded-xl border border-border-layout-1 p-4"
      data-testid="ai-key-routing"
    >
      <div className="space-y-1">
        <Text as="h3" level="label-small" className="text-content-layout-1">
          What each key covers
        </Text>
        <ul className="list-disc space-y-1 pl-5">
          {keyRoutingLines(provider, signedIn).map((line) => (
            <li key={line}>
              <Text
                as="span"
                level="body-small"
                className="text-content-layout-3"
              >
                {line}
              </Text>
            </li>
          ))}
        </ul>
      </div>
      {signedIn && limit.data?.known ? (
        <MonitoringLimitLine limit={limit.data} />
      ) : null}
      <AllowanceNotice limit={limit.data} />
    </div>
  )
}
