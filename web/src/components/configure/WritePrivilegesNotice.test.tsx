import { describe, expect, it } from 'vitest'
import { readOnlySql } from './WritePrivilegesNotice'

describe('readOnlySql', () => {
  it('generates administrator-reviewed PostgreSQL role SQL', () => {
    const sql = readOnlySql('postgresql', 'customer-prod')

    expect(sql).toContain('CREATE ROLE rdst_readonly')
    expect(sql).toContain('NOSUPERUSER')
    expect(sql).toContain('NOCREATEDB')
    expect(sql).toContain('NOCREATEROLE')
    expect(sql).toContain('NOREPLICATION')
    expect(sql).toContain('NOBYPASSRLS')
    expect(sql).toContain('CONNECTION LIMIT 20')
    expect(sql).toContain('SET default_transaction_read_only = on')
    expect(sql).not.toContain('statement_timeout')
    expect(sql).toContain('GRANT CONNECT ON DATABASE "customer-prod"')
    expect(sql).toContain('GRANT SELECT ON ALL TABLES IN SCHEMA public')
    expect(sql).toContain('GRANT pg_read_all_stats TO rdst_readonly')
    expect(sql).toContain('GRANT pg_monitor TO rdst_readonly')
    expect(sql).not.toContain('ALTER DEFAULT PRIVILEGES')
    expect(sql).not.toMatch(/GRANT\s+(?:INSERT|UPDATE|DELETE|ALL)\b/i)
    expect(sql.split('\n').length).toBeLessThanOrEqual(18)
  })

  it('quotes a PostgreSQL database identifier', () => {
    const sql = readOnlySql('postgresql', 'customer"prod')

    expect(sql).toContain('DATABASE "customer""prod"')
  })

  it('generates a host-scoped MySQL account with read-only grants', () => {
    const sql = readOnlySql('mysql', 'customer`prod')

    expect(sql).toContain(
      "CREATE USER 'rdst_readonly'@'replace_with_rdst_client_host'"
    )
    expect(sql).toContain('WITH MAX_USER_CONNECTIONS 20')
    expect(sql).toContain('GRANT SELECT, SHOW VIEW ON `customer``prod`.*')
    expect(sql).toContain('GRANT PROCESS ON *.*')
    expect(sql).toContain('GRANT SELECT ON performance_schema.*')
    expect(sql).toContain('GRANT REPLICATION CLIENT ON *.*')
    expect(sql).toContain('GRANT SELECT ON mysql.slow_log')
    expect(sql).not.toMatch(/GRANT\s+(?:INSERT|UPDATE|DELETE|ALL)\b/i)
    expect(sql).not.toContain('WITH GRANT OPTION')
    expect(sql).not.toContain('GRANT EXECUTE')
    expect(sql.split('\n').length).toBeLessThanOrEqual(20)
  })
})
