import { MySQL, PostgreSQL, sql } from '@codemirror/lang-sql'
import { EditorState, type Extension, RangeSetBuilder } from '@codemirror/state'
import {
  Decoration,
  type DecorationSet,
  EditorView,
  ViewPlugin,
  type ViewUpdate,
} from '@codemirror/view'
import { CopyButton } from '@rs/ui-new/copy-button'
import CodeMirror from '@uiw/react-codemirror'
import { useMemo } from 'react'
import {
  getParameterColor,
  PARAMETER_HIGHLIGHT_COLOR_COUNT,
  type ParameterHighlight,
} from './parameterHighlighting'
import { readonlyOverrides, sqlTheme } from './sqlTheme'

interface SQLDisplayProps {
  sql: string
  dialect?: 'postgresql' | 'mysql'
  className?: string
  /** Wrap long lines instead of horizontal scroll */
  wrap?: boolean
  /** Show copy button */
  showCopy?: boolean
  /** Optional parameter placeholders to highlight (e.g. :p1, $1) */
  parameterHighlights?: ParameterHighlight[]
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function buildParameterHighlightExtension(
  parameterHighlights: ParameterHighlight[]
): Extension[] {
  if (parameterHighlights.length === 0) {
    return []
  }

  const uniqueHighlights = new Map<string, number>()
  for (const highlight of parameterHighlights) {
    if (!uniqueHighlights.has(highlight.token)) {
      uniqueHighlights.set(highlight.token, highlight.colorIndex)
    }
  }

  if (uniqueHighlights.size === 0) {
    return []
  }

  const tokens = Array.from(uniqueHighlights.keys()).sort(
    (a, b) => b.length - a.length
  )
  const tokenRegex = new RegExp(tokens.map(escapeRegex).join('|'), 'g')
  const classByToken = new Map(
    Array.from(uniqueHighlights.entries()).map(([token, colorIndex]) => [
      token,
      `cm-param-highlight cm-param-highlight-${colorIndex % PARAMETER_HIGHLIGHT_COLOR_COUNT}`,
    ])
  )

  const highlightTheme = EditorView.baseTheme({
    '.cm-param-highlight': {
      borderRadius: '4px',
      padding: '0 1px',
      border: '1px solid transparent',
      fontWeight: '600',
    },
    ...Object.fromEntries(
      Array.from({ length: PARAMETER_HIGHLIGHT_COLOR_COUNT }, (_, index) => {
        const color = getParameterColor(index)
        return [
          `.cm-param-highlight-${index}`,
          {
            backgroundColor: color.codeBackground,
            borderColor: color.codeBorder,
            color: color.codeText,
          },
        ]
      })
    ),
  })

  const highlightPlugin = ViewPlugin.fromClass(
    class {
      decorations: DecorationSet

      constructor(view: EditorView) {
        this.decorations = this.buildDecorations(view)
      }

      update(update: ViewUpdate) {
        if (update.docChanged || update.viewportChanged) {
          this.decorations = this.buildDecorations(update.view)
        }
      }

      private buildDecorations(view: EditorView): DecorationSet {
        const builder = new RangeSetBuilder<Decoration>()
        const text = view.state.doc.toString()
        tokenRegex.lastIndex = 0

        for (
          let match = tokenRegex.exec(text);
          match;
          match = tokenRegex.exec(text)
        ) {
          const token = match[0]
          const cssClass = classByToken.get(token)
          if (!cssClass) {
            continue
          }

          builder.add(
            match.index,
            match.index + token.length,
            Decoration.mark({ class: cssClass })
          )
        }

        return builder.finish()
      }
    },
    {
      decorations: (value) => value.decorations,
    }
  )

  return [highlightTheme, highlightPlugin]
}

/**
 * Lightweight readonly SQL display with syntax highlighting.
 * Uses the same theme as SQLEditor for consistency.
 */
export function SQLDisplay({
  sql: sqlCode,
  dialect = 'postgresql',
  className = '',
  wrap = true,
  showCopy = false,
  parameterHighlights = [],
}: SQLDisplayProps) {
  const extensions = useMemo(() => {
    const sqlDialect = dialect === 'mysql' ? MySQL : PostgreSQL

    const exts = [
      sql({ dialect: sqlDialect }),
      readonlyOverrides,
      EditorState.readOnly.of(true),
      EditorView.editable.of(false),
    ]

    if (wrap) {
      exts.push(EditorView.lineWrapping)
    }

    exts.push(...buildParameterHighlightExtension(parameterHighlights))

    return exts
  }, [dialect, wrap, parameterHighlights])

  return (
    <div className={`relative ${className}`}>
      {showCopy && (
        <div className="absolute top-2 right-2 z-10">
          <CopyButton text={sqlCode} />
        </div>
      )}
      <CodeMirror
        value={sqlCode}
        extensions={extensions}
        theme={sqlTheme}
        editable={false}
        basicSetup={false}
      />
    </div>
  )
}
