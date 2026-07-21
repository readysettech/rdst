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
export const SQL_TOKEN_COLORS = {
  comment: '#6b7280',
  keyword: '#f472b6',
  string: '#86efac',
  quotedIdent: '#93c5fd',
  number: '#fcd34d',
  bool: '#fcd34d',
  null: '#fb923c',
  param: '#67e8f9',
  fn: '#c4b5fd',
  punctuation: '#64748b',
  identifier: '#e8e8e8',
} as const
