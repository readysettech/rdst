/**
 * Lightweight, regex-level SQL syntax highlighter for card SQL.
 *
 * The canonical QueryCard shows full, syntax-highlighted SQL on EVERY row, and
 * lists reach 150+ rows (/top, /saved, /benchmark). Mounting a CodeMirror editor
 * per card (as the round-2 `SQLDisplay` path did) is both a perf and a
 * bundle-weight hazard — so card SQL renders through this instead: a single regex
 * pass → plain coloured <span>s, no editor, no per-card view instance.
 *
 * The palette mirrors the CodeMirror `sqlTheme` (./sqlTheme.ts) 1:1 so a card's
 * SQL reads identically to the /top expanded editor the owner approved — same
 * pink keywords, green strings, amber numbers, cyan params. Colours are applied
 * as inline `style` (not Tailwind classes) because these are the app's
 * established SQL-theme hues, not design-system surface/content tokens; keeping
 * them inline also leaves `check:tokens` (a className grep) untouched.
 *
 * Every source character is emitted, in order, inside the output — so the
 * element's `textContent` equals the SQL verbatim. That keeps copy-from-DOM,
 * screen-reader output and the e2e SQL hooks exact. [feedback-triage-2 §1.2;
 * VIS-011/017 SQL dominant, VIS-124 mono personality, USE-004 readable]
 */

import { type CSSProperties, useMemo } from 'react'
import { SQL_TOKEN_COLORS as COLOR } from './sqlTokenColors'

const KEYWORDS = new Set([
  'SELECT',
  'FROM',
  'WHERE',
  'AND',
  'OR',
  'NOT',
  'AS',
  'ON',
  'IN',
  'IS',
  'LIKE',
  'ILIKE',
  'BETWEEN',
  'JOIN',
  'INNER',
  'LEFT',
  'RIGHT',
  'FULL',
  'OUTER',
  'CROSS',
  'LATERAL',
  'NATURAL',
  'USING',
  'GROUP',
  'BY',
  'ORDER',
  'HAVING',
  'LIMIT',
  'OFFSET',
  'FETCH',
  'NEXT',
  'ROW',
  'ROWS',
  'DISTINCT',
  'UNION',
  'ALL',
  'INTERSECT',
  'EXCEPT',
  'INSERT',
  'INTO',
  'VALUES',
  'UPDATE',
  'SET',
  'DELETE',
  'CREATE',
  'TABLE',
  'INDEX',
  'VIEW',
  'MATERIALIZED',
  'DROP',
  'ALTER',
  'ADD',
  'COLUMN',
  'RENAME',
  'TO',
  'PRIMARY',
  'KEY',
  'FOREIGN',
  'REFERENCES',
  'UNIQUE',
  'DEFAULT',
  'CHECK',
  'CONSTRAINT',
  'CASE',
  'WHEN',
  'THEN',
  'ELSE',
  'END',
  'EXISTS',
  'ANY',
  'SOME',
  'ASC',
  'DESC',
  'WITH',
  'RECURSIVE',
  'RETURNING',
  'OVER',
  'PARTITION',
  'WINDOW',
  'FILTER',
  'WITHIN',
  'CAST',
  'NULLS',
  'FIRST',
  'LAST',
  'ONLY',
  'GRANT',
  'BEGIN',
  'COMMIT',
  'ROLLBACK',
  'EXPLAIN',
  'ANALYZE',
  'VACUUM',
  'TRUNCATE',
])

// Ordered alternation; every branch consumes at least one character, so the
// whole input is partitioned with no gaps.
//   1 comment  2 string/backtick  3 param  4 number  5 word  6 whitespace  7 other
const TOKEN_RE =
  /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*'|"(?:[^"]|"")*"|`(?:[^`]|``)*`)|(:[A-Za-z_]\w*|\$\d+|\?)|(\d+(?:\.\d+)?)|([A-Za-z_]\w*)|(\s+)|([^\s\w])/g

interface Token {
  text: string
  style?: CSSProperties
}

function tokenize(sql: string): Token[] {
  const tokens: Token[] = []
  TOKEN_RE.lastIndex = 0
  let match = TOKEN_RE.exec(sql)
  while (match) {
    const [full, comment, str, param, num, word, ws] = match
    if (comment) {
      tokens.push({
        text: full,
        style: { color: COLOR.comment, fontStyle: 'italic' },
      })
    } else if (str) {
      const color = str[0] === '"' ? COLOR.quotedIdent : COLOR.string
      tokens.push({ text: full, style: { color } })
    } else if (param) {
      tokens.push({
        text: full,
        style: { color: COLOR.param, fontWeight: 600 },
      })
    } else if (num) {
      tokens.push({ text: full, style: { color: COLOR.number } })
    } else if (word) {
      const upper = word.toUpperCase()
      if (upper === 'NULL') {
        tokens.push({ text: full, style: { color: COLOR.null } })
      } else if (upper === 'TRUE' || upper === 'FALSE') {
        tokens.push({ text: full, style: { color: COLOR.bool } })
      } else if (KEYWORDS.has(upper)) {
        tokens.push({
          text: full,
          style: { color: COLOR.keyword, fontWeight: 500 },
        })
      } else {
        // Function call = identifier immediately followed by `(` (skipping spaces).
        const isFn = /^\s*\(/.test(sql.slice(match.index + full.length))
        tokens.push(
          isFn ? { text: full, style: { color: COLOR.fn } } : { text: full }
        )
      }
    } else if (ws) {
      tokens.push({ text: full })
    } else {
      tokens.push({ text: full, style: { color: COLOR.punctuation } })
    }
    // Zero-length safety (the regex never matches empty, but guard anyway).
    if (match.index === TOKEN_RE.lastIndex) TOKEN_RE.lastIndex += 1
    match = TOKEN_RE.exec(sql)
  }
  return tokens
}

interface SqlTokensProps {
  sql: string
  /** Full SQL as a `title` tooltip + stable DOM hook for the card's e2e locators. */
  title?: string
}

export function SqlTokens({ sql, title }: SqlTokensProps) {
  const tokens = useMemo(() => tokenize(sql), [sql])
  return (
    <code
      title={title}
      className="block font-mono text-mono-medium leading-relaxed whitespace-pre-wrap break-words"
      style={{ color: COLOR.identifier }}
    >
      {tokens.map((token, i) => (
        <span key={i} style={token.style}>
          {token.text}
        </span>
      ))}
    </code>
  )
}
