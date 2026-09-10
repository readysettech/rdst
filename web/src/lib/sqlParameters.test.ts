import { describe, expect, it } from 'vitest'

import {
  detectParameters,
  fillCapturedParams,
  findResidualPlaceholders,
  formatValue,
  hasParameters,
  hasResidualPlaceholders,
  substituteParameters,
  toBackendParams,
} from './sqlParameters'

describe('detectParameters', () => {
  const placeholders = (sql: string) =>
    detectParameters(sql).map((parameter) => parameter.placeholder)

  it('detects each supported placeholder style', () => {
    expect(placeholders('SELECT * FROM t WHERE a = $1 AND b = $2')).toEqual([
      '$1',
      '$2',
    ])
    expect(placeholders('SELECT * FROM t WHERE a = ? AND b = ?')).toEqual([
      '?',
      '?',
    ])
    expect(
      placeholders('SELECT * FROM t WHERE a = :name AND b = @handle')
    ).toEqual([':name', '@handle'])
  })

  it('does not read a :: type cast as a named placeholder', () => {
    expect(placeholders('SELECT a::text FROM t WHERE b = 1')).toEqual([])
  })

  it('reads no placeholders inside string literals, quoted names or comments', () => {
    expect(
      placeholders("SELECT * FROM t WHERE note = 'ping @bob about $1'")
    ).toEqual([])
    expect(placeholders('SELECT "@col" FROM t')).toEqual([])
    expect(placeholders('SELECT 1 -- todo: ask @bob about $1\nFROM t')).toEqual(
      []
    )
    expect(placeholders('SELECT 1 /* $1 :p1 */ FROM t')).toEqual([])
    expect(placeholders('SELECT $tag$ $1 :p1 @bob $tag$ FROM t')).toEqual([])
  })

  it('still reads the real slots around a literal that looks like one', () => {
    expect(
      placeholders("SELECT * FROM t WHERE note = 'see $9' AND a = $1")
    ).toEqual(['$1'])
  })
})

// Mike #11 / B-01: the value shapes that used to be re-read as parameters and
// dead-ended the analyze flow. Every one of them must round trip to SQL that
// asks for nothing further.
describe('parameter values survive the substitution round trip', () => {
  const SQL = 'SELECT id FROM users WHERE email = :p1'
  const shapes: Array<[string, string]> = [
    ['email', 'alice@example.com'],
    ['dollar amount', '$100 refund'],
    ['colon word', 'note:urgent'],
    ['at handle', '@handle'],
    ['question', 'why?'],
    ['plain word', 'alice'],
    ['single-quoted', "'alice'"],
    ['double-quoted', '"alice"'],
    ['apostrophe', "O'Brien"],
    ['dollar quoting', '$tag$ alice $tag$'],
    ['percent wildcard', '%alice%'],
    ['unicode', 'Unicode unlu cagri 名前'],
    ['emoji', 'ok 🎉 done'],
    ['leading-zero number', '00042'],
    ['plain number', '42'],
    ['very long', 'a'.repeat(500)],
    ['whitespace', '   '],
    ['sql comment', 'a -- $1'],
    ['every style at once', '$1 :p1 @p1 ?'],
  ]

  it.each(shapes)('%s', (_name, value) => {
    const parameters = detectParameters(SQL)
    const substituted = substituteParameters(SQL, parameters, { ':p1': value })
    expect(hasParameters(substituted)).toBe(false)
    expect(findResidualPlaceholders(substituted)).toEqual([])
  })
})

describe('formatValue', () => {
  it('uses bare numbers, NULL, TRUE and FALSE as entered', () => {
    expect(formatValue('42')).toBe('42')
    expect(formatValue('-8.5')).toBe('-8.5')
    expect(formatValue('null')).toBe('NULL')
    expect(formatValue(' true ')).toBe('TRUE')
  })

  it('keeps digits with a leading zero as text so they are not truncated', () => {
    expect(formatValue('00042')).toBe("'00042'")
  })

  it('treats a double-quoted value as a string, not a column name', () => {
    expect(formatValue('"alice"')).toBe("'alice'")
  })

  it('passes a single-quoted value through and escapes a bare apostrophe', () => {
    expect(formatValue("'alice'")).toBe("'alice'")
    expect(formatValue("O'Brien")).toBe("'O''Brien'")
    expect(formatValue("'")).toBe("''''")
  })
})

describe('fillCapturedParams', () => {
  it('fills named placeholders from captured values, in any order', () => {
    const sql = 'SELECT count(*) FROM t WHERE rating > :p2 LIMIT :p1'
    expect(fillCapturedParams(sql, { p1: '100', p2: '8.5' })).toBe(
      'SELECT count(*) FROM t WHERE rating > 8.5 LIMIT 100'
    )
  })

  it('fills positional $N placeholders', () => {
    const sql = 'SELECT * FROM t WHERE a > $1 AND b < $2'
    expect(fillCapturedParams(sql, { p1: '5', p2: '10' })).toBe(
      'SELECT * FROM t WHERE a > 5 AND b < 10'
    )
  })

  it('quotes non-numeric string values', () => {
    expect(
      fillCapturedParams('SELECT * FROM t WHERE name = :p1', { p1: 'Alice' })
    ).toBe("SELECT * FROM t WHERE name = 'Alice'")
  })

  it('returns the SQL unchanged when there are no parameters', () => {
    const sql = 'SELECT count(*) FROM t WHERE rating > 8.5'
    expect(fillCapturedParams(sql, { p1: 'x' })).toBe(sql)
  })

  it('leaves placeholders untouched when no captured values exist', () => {
    const sql = 'SELECT * FROM t WHERE a > :p1'
    expect(fillCapturedParams(sql, {})).toBe(sql)
    expect(fillCapturedParams(sql, undefined)).toBe(sql)
  })

  it('fills only the placeholders it has values for, leaving the rest', () => {
    const sql = 'SELECT * FROM t WHERE a > :p1 AND b < :p2'
    expect(fillCapturedParams(sql, { p1: '5' })).toBe(
      'SELECT * FROM t WHERE a > 5 AND b < :p2'
    )
  })
})

describe('substituteParameters', () => {
  it('does not let $1 mangle $10 (or vice versa)', () => {
    const sql = 'SELECT * FROM t WHERE a = $1 AND b = $10'
    const parameters = detectParameters(sql)
    expect(substituteParameters(sql, parameters, { $1: '5', $10: '99' })).toBe(
      'SELECT * FROM t WHERE a = 5 AND b = 99'
    )
  })

  it('does not let :p1 mangle :p10 (or vice versa)', () => {
    const sql = 'SELECT * FROM t WHERE a = :p1 AND b = :p10'
    const parameters = detectParameters(sql)
    expect(
      substituteParameters(sql, parameters, { ':p1': '5', ':p10': '99' })
    ).toBe('SELECT * FROM t WHERE a = 5 AND b = 99')
  })

  it('substitutes a slot adjacent to punctuation', () => {
    const sql = 'SELECT * FROM t WHERE a IN ($1, $2)'
    const parameters = detectParameters(sql)
    expect(substituteParameters(sql, parameters, { $1: '5', $2: '10' })).toBe(
      'SELECT * FROM t WHERE a IN (5, 10)'
    )
  })

  it('substitutes a slot immediately followed by a cast', () => {
    const sql = 'SELECT * FROM t WHERE a = $1::int AND b = $10::text'
    const parameters = detectParameters(sql)
    expect(substituteParameters(sql, parameters, { $1: '5', $10: 'x' })).toBe(
      "SELECT * FROM t WHERE a = 5::int AND b = 'x'::text"
    )
  })

  it('leaves placeholder-shaped text inside a string literal untouched', () => {
    const sql = "SELECT * FROM t WHERE note = 'contact $1' AND b = $1"
    const parameters = detectParameters(sql)
    expect(substituteParameters(sql, parameters, { $1: '5' })).toBe(
      "SELECT * FROM t WHERE note = 'contact $1' AND b = 5"
    )
  })

  it('substitutes MySQL ? placeholders in order without disturbing others', () => {
    const sql = 'SELECT * FROM t WHERE a = ? AND b = ?'
    const parameters = detectParameters(sql)
    expect(
      substituteParameters(sql, parameters, { '?1': '5', '?2': '10' })
    ).toBe('SELECT * FROM t WHERE a = 5 AND b = 10')
  })
})

describe('toBackendParams', () => {
  it('normalizes positional and named placeholders to backend keys', () => {
    const sql = 'SELECT * FROM t WHERE a = $1 AND b = :name'
    const parameters = detectParameters(sql)
    expect(toBackendParams(parameters, { $1: '5', ':name': 'Alice' })).toEqual({
      p1: '5',
      name: 'Alice',
    })
  })

  it('normalizes ? placeholders to backend keys by occurrence order', () => {
    const sql = 'SELECT * FROM t WHERE a = ? AND b = ?'
    const parameters = detectParameters(sql)
    expect(toBackendParams(parameters, { '?1': '5', '?2': '10' })).toEqual({
      p1: '5',
      p2: '10',
    })
  })

  it('drops blank values instead of persisting empty strings', () => {
    const sql = 'SELECT * FROM t WHERE a = $1 AND b = $2'
    const parameters = detectParameters(sql)
    expect(toBackendParams(parameters, { $1: '5', $2: '  ' })).toEqual({
      p1: '5',
    })
  })
})

describe('findResidualPlaceholders / hasResidualPlaceholders', () => {
  it('finds no residual placeholders in a fully substituted query', () => {
    const sql = "SELECT * FROM t WHERE a = 5 AND b = 'Alice'"
    expect(findResidualPlaceholders(sql)).toEqual([])
    expect(hasResidualPlaceholders(sql)).toBe(false)
  })

  it('finds an unsubstituted positional placeholder', () => {
    const sql = 'SELECT * FROM t WHERE a = 5 AND b = $2'
    expect(findResidualPlaceholders(sql)).toEqual(['$2'])
    expect(hasResidualPlaceholders(sql)).toBe(true)
  })

  it('finds an unsubstituted named and ? placeholder', () => {
    const sql = 'SELECT * FROM t WHERE a = :name AND b = ?'
    expect(findResidualPlaceholders(sql).sort()).toEqual([':name', '?'])
  })

  it('ignores placeholder-shaped text inside a string literal', () => {
    const sql = "SELECT * FROM t WHERE note = 'contact $1 or :fallback'"
    expect(findResidualPlaceholders(sql)).toEqual([])
  })

  it('does not mistake a :: type cast for a named placeholder', () => {
    const sql = 'SELECT * FROM t WHERE a = 5::integer'
    expect(findResidualPlaceholders(sql)).toEqual([])
  })

  it('handles a doubled quote inside a string literal without losing state', () => {
    const sql = "SELECT * FROM t WHERE note = 'it''s $1' AND b = $2"
    expect(findResidualPlaceholders(sql)).toEqual(['$2'])
  })
})
