import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetAuditPreflightCacheForTests,
  checkAuditPreflight,
  isAuditPreflightBlocked,
} from './auditPreflight'

beforeEach(__resetAuditPreflightCacheForTests)

describe('audit preflight', () => {
  it('hard-gates query statistics and Docker for live captures only', async () => {
    const fetcher = vi.fn(async (target: string) => ({
      target,
      engine: 'postgresql',
      query_stats: target === 'missing' ? 'missing' : 'ok',
      detail: target === 'missing' ? 'extension missing' : 'ready',
      remediation: target === 'missing' ? 'CREATE EXTENSION pg_stat_statements;' : null,
      docker_available: false,
    }))
    const blocked = await checkAuditPreflight(['ok', 'missing'], { fetcher })
    expect(isAuditPreflightBlocked(blocked)).toBe(true)
    expect(
      isAuditPreflightBlocked(blocked, { requireQueryStats: false })
    ).toBe(false)

    const allowed = await checkAuditPreflight(['ok'], { fetcher, force: true })
    expect(isAuditPreflightBlocked(allowed)).toBe(true)
    expect(
      isAuditPreflightBlocked(allowed, { requireQueryStats: false })
    ).toBe(false)
    expect(allowed.requirements.ok.docker_available).toBe(false)
  })

  it('hard-gates a missing AWS session only for AWS selections', async () => {
    const fetcher = vi.fn(async (target: string) => ({
      target,
      engine: 'postgresql',
      query_stats: 'ok' as const,
      detail: 'ready',
      docker_available: true,
    }))
    const awsFetcher = vi.fn(async () => ({
      has_credentials: false,
      method: null,
      identity_arn: null,
      account: null,
      active_profile: null,
      available_profiles: ['dev'],
      region: null,
    }))

    const local = await checkAuditPreflight(['local'], { fetcher })
    expect(isAuditPreflightBlocked(local)).toBe(false)
    expect(awsFetcher).not.toHaveBeenCalled()

    const aws = await checkAuditPreflight(['rds'], {
      fetcher,
      force: true,
      awsRequired: true,
      awsFetcher,
    })
    expect(aws.aws.required).toBe(true)
    expect(isAuditPreflightBlocked(aws)).toBe(true)
    expect(awsFetcher).toHaveBeenCalledOnce()
  })

  it('blocks password failures and AWS account mismatches', () => {
    expect(
      isAuditPreflightBlocked({
        requirements: {},
        errors: {
          orders: {
            code: 'TARGET_PASSWORD_REQUIRED',
            message: 'Password required',
            target: 'orders',
          },
        },
        aws: { required: false },
      })
    ).toBe(true)

    expect(
      isAuditPreflightBlocked({
        requirements: {
          orders: {
            target: 'orders',
            engine: 'postgresql',
            query_stats: 'ok',
            detail: 'ready',
            docker_available: true,
          },
        },
        errors: {},
        aws: {
          required: true,
          status: {
            has_credentials: true,
            method: 'sso',
            identity_arn: 'arn:aws:sts::111:assumed-role/dev/user',
            account: '111',
            active_profile: 'dev',
            available_profiles: ['dev'],
            region: 'us-east-1',
          },
          mismatchedTargets: ['orders'],
          mismatchedAccount: '222',
        },
      })
    ).toBe(true)
  })

  it('re-resolves the selected AWS profile account on every check', async () => {
    const fetcher = vi.fn(async (target: string) => ({
      target,
      engine: 'postgresql',
      query_stats: 'ok' as const,
      detail: 'ready',
      docker_available: true,
    }))
    const accounts = { old: '111', current: '222' }
    const awsFetcher = vi.fn(async (profile?: string) => ({
      has_credentials: true,
      method: 'sso',
      identity_arn: `arn:aws:sts::${accounts[profile as keyof typeof accounts]}:assumed-role/dev/user`,
      account: accounts[profile as keyof typeof accounts],
      active_profile: profile ?? null,
      available_profiles: ['old', 'current'],
      region: 'us-east-1',
    }))

    const stale = await checkAuditPreflight(['orders'], {
      fetcher,
      force: true,
      awsRequired: true,
      awsProfile: 'old',
      awsFetcher,
      targetAccounts: { orders: '222' },
    })
    const fresh = await checkAuditPreflight(['orders'], {
      fetcher,
      force: true,
      awsRequired: true,
      awsProfile: 'current',
      awsFetcher,
      targetAccounts: { orders: '222' },
    })

    expect(stale.aws.status?.account).toBe('111')
    expect(stale.aws.mismatchedTargets).toEqual(['orders'])
    expect(fresh.aws.status?.account).toBe('222')
    expect(fresh.aws.mismatchedTargets).toBeUndefined()
    expect(awsFetcher.mock.calls).toEqual([['old'], ['current']])
  })

  it('caps concurrency at three and caches results for sixty seconds', async () => {
    let active = 0
    let maxActive = 0
    let release: (() => void)[] = []
    const fetcher = vi.fn(async (target: string) => {
      active += 1
      maxActive = Math.max(maxActive, active)
      await new Promise<void>((resolve) => release.push(resolve))
      active -= 1
      return {
        target,
        engine: 'postgresql',
        query_stats: 'ok',
        detail: 'ready',
        docker_available: true,
      }
    })
    let now = 1_000
    const pending = checkAuditPreflight(['a', 'b', 'c', 'd'], {
      fetcher,
      now: () => now,
    })
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3))
    expect(maxActive).toBe(3)
    release.splice(0).forEach((resolve) => resolve())
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4))
    release.splice(0).forEach((resolve) => resolve())
    await pending

    await checkAuditPreflight(['a', 'b'], { fetcher, now: () => now })
    expect(fetcher).toHaveBeenCalledTimes(4)
    now += 60_001
    const expired = checkAuditPreflight(['a'], { fetcher, now: () => now })
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(5))
    release.splice(0).forEach((resolve) => resolve())
    await expired
  })
})
