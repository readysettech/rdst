import { describe, expect, it } from 'vitest'
import { parseConnectionUrl } from './connectionUrl'

describe('parseConnectionUrl', () => {
  it('URL-decodes the database name as it does the credentials', () => {
    const parsed = parseConnectionUrl(
      'postgresql://app%40corp:p%2Fss@db.example.com:6543/%E7%8C%AB?sslmode=require' // trufflehog:ignore
    )
    expect(parsed).toMatchObject({
      engine: 'postgresql',
      host: 'db.example.com',
      port: 6543,
      user: 'app@corp',
      password: 'p/ss',
      database: '\u732b',
      tls: true,
    })
    expect(parseConnectionUrl('mysql://root@localhost/my%20db')?.database).toBe(
      'my db'
    )
  })

  it('keeps the raw text of a malformed % sequence', () => {
    const parsed = parseConnectionUrl('postgres://u%zz:pw@localhost/sales%zz')
    expect(parsed?.database).toBe('sales%zz')
    expect(parsed?.user).toBe('u%zz')
  })
})
