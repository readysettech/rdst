import { describe, expect, it } from 'vitest'
import { type FoundDatabase, recommended } from '../../lib/agentDiscovery'
import {
  agentCanSetUp,
  countLabel,
  groupByProject,
  projectLabel,
} from './FindDatabases'

const found = (
  id: string,
  provider: FoundDatabase['provider'],
  environment: FoundDatabase['environment'] = 'development'
): FoundDatabase => ({
  id,
  name: id,
  engine: 'postgresql',
  host: 'h',
  port: 5432,
  database: 'd',
  user: 'u',
  provider,
  environment,
  project: null,
  source: null,
  env_file: null,
  env_var: null,
  password_file: null,
  password_var: null,
  note: null,
  found_by: [],
})

describe('found databases', () => {
  it("sends a provider's database to its sign-in first, whatever was found", () => {
    const withUrl = {
      ...found('rds', 'aws'),
      env_file: '/w/.env',
      env_var: 'DB_URL',
    }
    expect(agentCanSetUp(withUrl)).toBe(false)
    expect(agentCanSetUp(found('local', 'docker'))).toBe(true)
    expect(agentCanSetUp(found('other', 'other'))).toBe(true)
  })

  it('picks the most recent one the agent can set up, never production', () => {
    expect(
      recommended([
        found('cloud', 'supabase'),
        found('prod', 'local', 'production'),
        found('dev', 'docker'),
      ])?.id
    ).toBe('dev')
    expect(recommended([found('cloud', 'neon')])).toBeNull()
  })

  it("shows WSL's folders as WSL paths", () => {
    expect(
      projectLabel('\\\\wsl.localhost\\Ubuntu\\home\\me\\projects\\shop')
    ).toBe('WSL ~/projects/shop')
    expect(projectLabel('C:\\Users\\me\\work\\shop')).toBe('~\\work\\shop')
  })

  it('counts in words up to nine', () => {
    expect(countLabel(1)).toBe('one database')
    expect(countLabel(4)).toBe('four databases')
    expect(countLabel(20)).toBe('20 databases')
    expect(countLabel(9, 'step')).toBe('nine steps')
  })

  it('groups by project, then the rest here, then what only history saw', () => {
    const inShop = { ...found('a', 'docker'), project: '/w/shop' }
    const running = found('b', 'local')
    const seen = { ...found('c', 'neon'), project: '/w/shop', history: true }
    const inApi = { ...found('d', 'local'), project: '/w/api' }
    const groups = groupByProject([inShop, running, seen, inApi])
    expect(
      groups.map((group) => [
        group.kind,
        group.project,
        group.items.map((i) => i.id),
      ])
    ).toEqual([
      ['project', '/w/shop', ['a']],
      ['project', '/w/api', ['d']],
      ['elsewhere', null, ['b']],
      ['history', null, ['c']],
    ])
  })
})
