import {
  AuditRequirementsError,
  fetchAuditRequirements,
  type AuditRequirements,
} from './useAudit'
import {
  fetchFleetAwsStatus,
  type FleetAwsStatus,
} from './useFleet'

const CACHE_TTL_MS = 60_000
const cache = new Map<string, { checkedAt: number; value: AuditRequirements }>()

export interface AuditPreflightResult {
  requirements: Record<string, AuditRequirements>
  errors: Record<string, AuditPreflightError>
  aws: {
    required: boolean
    status?: FleetAwsStatus
    error?: string
    // Targets stamped (via their aws-account tag) with a different AWS
    // account than the signed-in session. Checks can't span accounts.
    mismatchedTargets?: string[]
    mismatchedAccount?: string
  }
}

export interface AuditPreflightError {
  code: string
  message: string
  target?: string
  passwordEnv?: string
}

export function isAuditPreflightBlocked(
  result: AuditPreflightResult,
  options: { requireQueryStats?: boolean; requireDocker?: boolean } = {}
): boolean {
  const requireQueryStats = options.requireQueryStats ?? true
  const requireDocker = options.requireDocker ?? requireQueryStats
  return (
    Object.keys(result.errors).length > 0 ||
    (requireQueryStats &&
      Object.values(result.requirements).some(
        ({ query_stats }) =>
          query_stats === 'missing' || query_stats === 'error'
      )) ||
    (requireDocker &&
      Object.values(result.requirements).some(
        ({ docker_available }) => !docker_available
      )) ||
    (result.aws.required &&
      (!!result.aws.error ||
        !result.aws.status?.has_credentials ||
        (result.aws.mismatchedTargets?.length ?? 0) > 0))
  )
}

/** Check targets with a small worker pool and a one-minute per-target cache. */
export async function checkAuditPreflight(
  targets: string[],
  options: {
    force?: boolean
    concurrency?: number
    now?: () => number
    fetcher?: (target: string) => Promise<AuditRequirements>
    awsRequired?: boolean
    awsProfile?: string
    awsFetcher?: (profile?: string) => Promise<FleetAwsStatus>
    // Target name -> AWS account id it was imported from (aws-account tag).
    targetAccounts?: Record<string, string>
  } = {}
): Promise<AuditPreflightResult> {
  const now = options.now ?? Date.now
  const fetcher = options.fetcher ?? fetchAuditRequirements
  const requirements: Record<string, AuditRequirements> = {}
  const errors: Record<string, AuditPreflightError> = {}
  const pending: string[] = []

  for (const target of Array.from(new Set(targets))) {
    const cached = cache.get(target)
    if (!options.force && cached && now() - cached.checkedAt < CACHE_TTL_MS) {
      requirements[target] = cached.value
    } else {
      pending.push(target)
    }
  }

  let next = 0
  const worker = async () => {
    while (next < pending.length) {
      const target = pending[next++]
      try {
        const value = await fetcher(target)
        requirements[target] = value
        cache.set(target, { checkedAt: now(), value })
      } catch (error) {
        if (error instanceof AuditRequirementsError) {
          errors[target] = {
            code: error.code,
            message: error.message,
            target: error.target ?? target,
            passwordEnv: error.passwordEnv,
          }
        } else {
          errors[target] = {
            code: 'PREFLIGHT_FAILED',
            message: error instanceof Error ? error.message : String(error),
            target,
          }
        }
      }
    }
  }
  await Promise.all(
    Array.from(
      { length: Math.min(options.concurrency ?? 3, pending.length) },
      worker
    )
  )
  const aws: AuditPreflightResult['aws'] = {
    required: options.awsRequired ?? false,
  }
  if (aws.required) {
    try {
      aws.status = await (options.awsFetcher ?? fetchFleetAwsStatus)(
        options.awsProfile
      )
    } catch (error) {
      aws.error = error instanceof Error ? error.message : String(error)
    }
    const signedAccount = aws.status?.account
    if (signedAccount && options.targetAccounts) {
      const mismatched = Object.entries(options.targetAccounts).filter(
        ([, account]) => account && account !== signedAccount
      )
      if (mismatched.length > 0) {
        aws.mismatchedTargets = mismatched.map(([name]) => name)
        aws.mismatchedAccount = mismatched[0][1]
      }
    }
  }
  return { requirements, errors, aws }
}

export function __resetAuditPreflightCacheForTests() {
  cache.clear()
}
