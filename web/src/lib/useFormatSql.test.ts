import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useFormatSql } from './useFormatSql'

async function formatted(sql: string, multilineProjection = false) {
  const { result } = renderHook(() =>
    useFormatSql(sql, 'postgresql', { lineWidth: 80, multilineProjection })
  )
  await waitFor(() => expect(result.current).not.toBeNull())
  return result.current
}

describe('useFormatSql', () => {
  it('keeps each clause on one line with its body', async () => {
    expect(
      await formatted(
        'SELECT id, name FROM users u LEFT JOIN orgs o ON o.id = u.org WHERE u.active AND o.paid ORDER BY name LIMIT 10'
      )
    ).toBe(
      [
        'SELECT id, name',
        'FROM users u',
        '  LEFT JOIN orgs o ON o.id = u.org',
        'WHERE u.active',
        '  AND o.paid',
        'ORDER BY name',
        'LIMIT 10',
      ].join('\n')
    )
  })

  it('lays out a nested query like the outer one', async () => {
    expect(
      await formatted(
        'WITH base AS (SELECT seg, debt FROM accounts a JOIN people p ON p.id = a.person) SELECT seg, AVG(debt) AS avg_debt FROM base GROUP BY seg',
        true
      )
    ).toBe(
      [
        'WITH base AS (',
        '    SELECT',
        '      seg,',
        '      debt',
        '    FROM accounts a',
        '      JOIN people p ON p.id = a.person',
        '  )',
        'SELECT',
        '  seg,',
        '  AVG(debt) AS avg_debt',
        'FROM base',
        'GROUP BY seg',
      ].join('\n')
    )
  })

  it('keeps an item of a long SELECT list on one line', async () => {
    expect(
      await formatted(
        'SELECT seg, COUNT(*) OVER (PARTITION BY seg) AS n FROM base',
        true
      )
    ).toBe(
      [
        'SELECT',
        '  seg,',
        '  COUNT(*) OVER ( PARTITION BY seg ) AS n',
        'FROM base',
      ].join('\n')
    )
  })
})
