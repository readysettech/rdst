/**
 * Single source of truth for the SQL syntax-highlight palette.
 *
 * Shared by the CodeMirror editor theme (./sqlTheme.ts) and the regex-level card
 * highlighter (./SqlTokens.tsx) so a card's SQL reads identically to the editor.
 * Kept in its own module with NO CodeMirror imports: SqlTokens renders on every
 * QueryCard, so importing from sqlTheme.ts (which pulls in @uiw/codemirror-themes,
 * @lezer/highlight and @codemirror/view) would drag CodeMirror into the eager
 * entry graph. [PS5 dedup item 3]
 */
/**
 * Hues mirror cloud's Prism SQL palette (ui-new `highlight.css`) so query text
 * reads the same across both apps: amber keywords, green strings, yellow
 * numbers, blue params/quoted identifiers, purple functions. The `var()`
 * entries resolve against the design-system theme at paint time, so they adapt
 * with it.
 */
export const SQL_TOKEN_COLORS = {
  comment: '#8da1b9',
  keyword: 'var(--color-content-warning-plain)',
  string: '#91d076',
  quotedIdent: '#6cb8e6',
  number: '#e6d37a',
  bool: '#e6d37a',
  null: '#e6d37a',
  param: '#6cb8e6',
  fn: '#c699e3',
  punctuation: 'var(--color-content-layout-2)',
  identifier: 'var(--color-content-layout-1)',
} as const
