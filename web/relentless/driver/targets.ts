import { execFile } from 'node:child_process'
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  writeFileSync,
} from 'node:fs'
import { join } from 'node:path'

import { paths } from './paths.ts'
import { isPsqlCommand } from './reach.ts'

/**
 * The disposable databases and SSH jump host a run can connect RDST to, and
 * real faults against them: pausing a container, or running SQL on the
 * database directly, as another application would.
 */
export type Service = 'postgres' | 'mysql' | 'bastion'

const env = process.env

export function databaseTargets() {
  return {
    postgres: {
      engine: 'postgresql',
      host: env.RDST_E2E_DB_HOST ?? '127.0.0.1',
      port: Number(env.RDST_E2E_DB_PORT ?? 15432),
      database: 'rdst_test',
      user: 'rdst_test',
      password: env.RDST_E2E_DB_PASSWORD ?? 'rdst_e2e_password',
      through_bastion: { host: 'postgres', port: 5432 },
    },
    mysql: {
      engine: 'mysql',
      host: env.RDST_RELENTLESS_MYSQL_HOST ?? '127.0.0.1',
      port: Number(env.RDST_RELENTLESS_MYSQL_PORT ?? 13307),
      database: 'testdb',
      user: 'testuser',
      password: 'testpassword',
      through_bastion: { host: 'mysql', port: 3306 },
    },
  }
}

const SSH_KEY_SOURCE =
  env.RDST_RELENTLESS_SSH_KEY_FILE ??
  join(paths.appDir, 'test-results', 'relentless-ssh', 'id_ed25519')

/**
 * Files the agent can hand to the app: a targets CSV for fleet import (two
 * good rows, a row missing its host, and a duplicate name) and a .sql file for
 * query import. They name this run's database addresses.
 */
export function writeFixtures(dir: string) {
  mkdirSync(dir, { recursive: true })
  const { postgres, mysql } = databaseTargets()
  writeFileSync(
    join(dir, 'targets.csv'),
    [
      'name,host,port,database,user,engine,group',
      `csv-postgres,${postgres.host},${postgres.port},${postgres.database},${postgres.user},postgresql,lab`,
      `csv-mysql,${mysql.host},${mysql.port},${mysql.database},${mysql.user},mysql,lab`,
      'csv-missing-host,,5432,x,y,postgresql,lab',
      `csv-postgres,${postgres.host},${postgres.port},${postgres.database},${postgres.user},postgresql,lab`,
      '',
    ].join('\n')
  )
  writeFileSync(
    join(dir, 'queries.sql'),
    [
      'SELECT count(*) FROM title_basics;',
      "SELECT primarytitle, startyear FROM title_basics WHERE titletype = 'movie' ORDER BY startyear DESC;",
      'SELECT b.primarytitle, r.averagerating FROM title_basics b JOIN title_ratings r ON r.tconst = b.tconst WHERE r.numvotes > 1000;',
      'SELEC broken statement;',
      '',
    ].join('\n')
  )
}

/** Copy the bastion's client key into the run's ~/.ssh, if a bastion runs. */
export function installBastionKey(home: string) {
  if (!existsSync(SSH_KEY_SOURCE)) return null
  const dir = join(home, '.ssh')
  mkdirSync(dir, { recursive: true, mode: 0o700 })
  const key = join(dir, 'id_ed25519')
  copyFileSync(SSH_KEY_SOURCE, key)
  copyFileSync(`${SSH_KEY_SOURCE}.pub`, `${key}.pub`)
  // As a person's own key would be; SSH ignores a private key others can read.
  chmodSync(key, 0o600)
  return {
    host: env.RDST_RELENTLESS_SSH_HOST ?? '127.0.0.1',
    port: Number(env.RDST_RELENTLESS_SSH_PORT ?? 12222),
    user: 'relentless',
    key_path: key,
  }
}

function docker(args: string[], timeoutMs = 60_000) {
  return new Promise<{ code: number; stdout: string; stderr: string }>(
    (resolve) => {
      execFile(
        'docker',
        args,
        { timeout: timeoutMs, maxBuffer: 4 * 1024 * 1024 },
        (error, stdout, stderr) => {
          const code =
            error && typeof error.code === 'number' ? error.code : error ? 1 : 0
          resolve({ code, stdout, stderr })
        }
      )
    }
  )
}

/**
 * The container behind a service: an explicit override, else the one the
 * relentless compose file started for it.
 */
async function containerFor(service: Service) {
  const override = env[`RDST_RELENTLESS_${service.toUpperCase()}_CONTAINER`]
  if (override) return override
  const listed = await docker([
    'ps',
    '--filter',
    `label=com.docker.compose.service=${service}`,
    '--format',
    '{{.Names}}\t{{.Label "com.docker.compose.project.config_files"}}',
  ])
  const match = listed.stdout
    .split('\n')
    .map((line) => line.split('\t'))
    .find(([, files]) => files?.includes('docker-compose.relentless.yml'))
  if (!match) {
    throw new Error(
      `no running ${service} container from docker-compose.relentless.yml; start it or set RDST_RELENTLESS_${service.toUpperCase()}_CONTAINER`
    )
  }
  return match[0]
}

/**
 * The PostgreSQL role the tester's SQL runs as. It owns the tables, so it can
 * index, alter and drop them, and it may tune settings, but it is not a
 * superuser: no server programs, no server files.
 */
const POSTGRES_TESTER = 'relentless_tester'

const POSTGRES_TESTER_SETUP = `
SET client_min_messages = warning;
DO $$
DECLARE r record;
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${POSTGRES_TESTER}') THEN
    CREATE ROLE ${POSTGRES_TESTER} LOGIN NOSUPERUSER;
  END IF;
  FOR r IN
    SELECT c.oid::regclass AS name FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
      AND pg_get_userbyid(c.relowner) <> '${POSTGRES_TESTER}'
  LOOP
    EXECUTE format('ALTER TABLE %s OWNER TO ${POSTGRES_TESTER}', r.name);
  END LOOP;
END $$;
GRANT pg_read_all_stats TO ${POSTGRES_TESTER};
GRANT CREATE, USAGE ON SCHEMA public TO ${POSTGRES_TESTER};
GRANT EXECUTE ON FUNCTION pg_reload_conf() TO ${POSTGRES_TESTER};
GRANT ALTER SYSTEM ON PARAMETER work_mem, maintenance_work_mem,
  effective_cache_size, random_page_cost, effective_io_concurrency,
  default_statistics_target, max_parallel_workers_per_gather, jit,
  log_min_duration_statement TO ${POSTGRES_TESTER};
`

/**
 * Hand the PostgreSQL fixture's tables to the tester role, including any a
 * workload created before the run. Without the compose databases there is
 * nothing to prepare, and `rt db exec` reports that itself.
 */
export async function prepareTesterRole() {
  let container: string
  try {
    container = await containerFor('postgres')
  } catch {
    return
  }
  const { user, database } = databaseTargets().postgres
  const result = await docker([
    'exec',
    container,
    'psql',
    '-X',
    '-U',
    user,
    '-d',
    database,
    '-v',
    'ON_ERROR_STOP=1',
    '-c',
    POSTGRES_TESTER_SETUP,
  ])
  if (result.code !== 0) {
    throw new Error(`preparing ${POSTGRES_TESTER}: ${result.stderr.trim()}`)
  }
}

/**
 * SQL as another application would run it: on PostgreSQL as the tester role,
 * on MySQL as the fixture's application user, or with `admin` as MySQL's
 * administrator, the way an operator applies a fix the app recommends. Only
 * SQL reaches the database: the command-line clients' own commands, which
 * run a shell in the database's container, are refused or turned off.
 */
export async function execSql(
  service: 'postgres' | 'mysql',
  sql: string,
  admin = false
) {
  const container = await containerFor(service)
  const target =
    admin && service === 'mysql'
      ? {
          ...databaseTargets().mysql,
          user: 'root',
          password: env.RDST_RELENTLESS_MYSQL_ROOT_PASSWORD ?? 'rootpassword',
        }
      : databaseTargets()[service]
  if (service === 'postgres' && isPsqlCommand(sql)) {
    return {
      code: 2,
      stdout: '',
      stderr: 'only SQL runs here, not psql commands',
    }
  }
  const command =
    service === 'postgres'
      ? [
          'psql',
          '-X',
          '-U',
          POSTGRES_TESTER,
          '-d',
          target.database,
          '-v',
          'ON_ERROR_STOP=1',
          '-c',
          sql,
        ]
      : [
          'mysql',
          '--skip-commands',
          `-u${target.user}`,
          target.database,
          '-e',
          sql,
        ]
  return docker([
    'exec',
    '-e',
    `MYSQL_PWD=${target.password}`,
    container,
    ...command,
  ])
}

export async function setPaused(service: Service, paused: boolean) {
  const container = await containerFor(service)
  const result = await docker([paused ? 'pause' : 'unpause', container])
  if (result.code !== 0) throw new Error(result.stderr.trim())
  return container
}
