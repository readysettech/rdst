import { afterEach, beforeEach, expect, it } from 'vitest'
import {
  cancelAnalyzeJob,
  cancelQueryAnalysis,
  enqueueAnalysis,
  getQueries,
  getQuery,
  resetStubState,
  setFixtureTiming,
} from './api'
import { setFixtureOverride } from './contract'
import { CA_KEYS } from './fixturesCa'

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('ca-database')
})

afterEach(() => setFixtureOverride(undefined))

it('a cancelled analysis returns the row to its previous status', async () => {
  const key = CA_KEYS.postsByOwneruserid
  const before = await getQuery('demo', key, '1h')
  expect(before.status).toBe('fix_suggested')
  await enqueueAnalysis('demo', key)
  const queued = await getQuery('demo', key, '1h')
  expect(queued.status).toBe('analyzing')
  expect(queued.last_analysis?.state).toBe('queued')
  expect(queued.last_analysis?.summary?.score).toBe(72)
  expect(
    (await getQueries('demo', '1h', 100)).queries.find((row) => row.key === key)
      ?.status
  ).toBe('analyzing')
  const job = await cancelQueryAnalysis('demo', key)
  expect(job).toMatchObject({
    query_key: key,
    state: 'skipped',
    error: 'cancelled',
    cancelled: true,
    reason_text: 'Cancelled.',
  })
  const after = await getQuery('demo', key, '1h')
  expect(after.status).toBe('fix_suggested')
  expect(after.last_analysis).toEqual(before.last_analysis)
  await expect(cancelQueryAnalysis('demo', key)).rejects.toMatchObject({
    status: 409,
  })
})

it('cancelling by job id takes the queued key out too', async () => {
  const key = CA_KEYS.votesByPostid
  const job = await enqueueAnalysis('demo', key)
  expect((await cancelAnalyzeJob('demo', job.id)).query_key).toBe(key)
  expect((await getQuery('demo', key, '1h')).status).toBe('fix_suggested')
  await expect(cancelAnalyzeJob('demo', job.id)).rejects.toMatchObject({
    status: 409,
  })
})
