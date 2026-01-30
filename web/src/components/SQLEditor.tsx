import { useCallback, useMemo, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { sql, PostgreSQL, MySQL } from "@codemirror/lang-sql";
import { EditorView, keymap } from "@codemirror/view";
import {
  autocompletion,
  startCompletion,
  acceptCompletion,
} from "@codemirror/autocomplete";
import { linter, type Diagnostic } from "@codemirror/lint";
import {
  format as formatSQL,
  type FormatOptionsWithLanguage,
} from "sql-formatter";
import { Button } from "@rs/ui-new/button";
import { Icon } from "@rs/ui-new/icon";
import { Text } from "@rs/ui-new/text";
import { HStack } from "@rs/ui-new/stack";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@rs/ui-new/tooltip";
import { editorOverrides, sqlTheme } from "./sqlTheme";

export interface SchemaInfo {
  tables: Record<string, string[]>;
  dialect?: "postgresql" | "mysql";
}

export function formatQuery(
  query: string,
  dialect?: "postgresql" | "mysql",
): string {
  if (!query.trim()) return query;

  try {
    const options: FormatOptionsWithLanguage = {
      language: dialect === "mysql" ? "mysql" : "postgresql",
      keywordCase: "upper",
      tabWidth: 2,
      useTabs: false,
    };
    return formatSQL(query, options);
  } catch {
    return query;
  }
}

function createSQLLinter(schema?: SchemaInfo) {
  return linter(
    (view) => {
      const diagnostics: Diagnostic[] = [];
      const text = view.state.doc.toString();

      if (!text.trim()) return diagnostics;

      const singleQuotes = (text.match(/'/g) || []).length;
      if (singleQuotes % 2 !== 0) {
        const lastQuote = text.lastIndexOf("'");
        diagnostics.push({
          from: lastQuote,
          to: lastQuote + 1,
          severity: "error",
          message: "Unclosed string literal",
          source: "sql-lint",
        });
      }

      const doubleQuotes = (text.match(/"/g) || []).length;
      if (doubleQuotes % 2 !== 0) {
        const lastQuote = text.lastIndexOf('"');
        diagnostics.push({
          from: lastQuote,
          to: lastQuote + 1,
          severity: "error",
          message: "Unclosed identifier quote",
          source: "sql-lint",
        });
      }

      let parenDepth = 0;
      let lastOpenParen = -1;
      for (let i = 0; i < text.length; i++) {
        if (text[i] === "(") {
          parenDepth++;
          lastOpenParen = i;
        } else if (text[i] === ")") {
          parenDepth--;
          if (parenDepth < 0) {
            diagnostics.push({
              from: i,
              to: i + 1,
              severity: "error",
              message: "Unmatched closing parenthesis",
              source: "sql-lint",
            });
            parenDepth = 0;
          }
        }
      }
      if (parenDepth > 0 && lastOpenParen >= 0) {
        diagnostics.push({
          from: lastOpenParen,
          to: lastOpenParen + 1,
          severity: "error",
          message: "Unclosed parenthesis",
          source: "sql-lint",
        });
      }

      const upperText = text.toUpperCase();

      if (
        /\bSELECT\b/.test(upperText) &&
        !/\bFROM\b/.test(upperText) &&
        !/\bSELECT\s+\d|SELECT\s+'/i.test(text) &&
        !/\bSELECT\s+NOW\s*\(|SELECT\s+CURRENT_|SELECT\s+VERSION\s*\(/i.test(
          text,
        )
      ) {
        if (/\bSELECT\s+\w+\./i.test(text)) {
          diagnostics.push({
            from: 0,
            to: text.indexOf(" ") > 0 ? text.indexOf(" ") : 6,
            severity: "warning",
            message: "SELECT with table-qualified column but no FROM clause",
            source: "sql-lint",
          });
        }
      }

      const whereMatch = /\bWHERE\s+(\w+)\s*$/i.exec(text);
      if (whereMatch) {
        const wherePos = text.toUpperCase().indexOf("WHERE");
        diagnostics.push({
          from: wherePos,
          to: text.length,
          severity: "warning",
          message: "WHERE clause appears incomplete",
          source: "sql-lint",
        });
      }

      if (schema?.tables) {
        const tableNames = new Set(
          Object.keys(schema.tables).map((t) => t.toLowerCase()),
        );
        const fromMatch = text.match(/\bFROM\s+(\w+)/gi);
        const joinMatch = text.match(/\bJOIN\s+(\w+)/gi);

        const checkTableRef = (match: string) => {
          const tableName = match.replace(/^(FROM|JOIN)\s+/i, "").toLowerCase();
          if (
            tableName &&
            !tableNames.has(tableName) &&
            !["select", "where", "order", "group", "limit"].includes(tableName)
          ) {
            const idx = text.toLowerCase().indexOf(match.toLowerCase());
            diagnostics.push({
              from: idx + match.length - tableName.length,
              to: idx + match.length,
              severity: "warning",
              message: `Unknown table: "${tableName}"`,
              source: "sql-lint",
            });
          }
        };

        fromMatch?.forEach(checkTableRef);
        joinMatch?.forEach(checkTableRef);
      }

      return diagnostics;
    },
    {
      delay: 300,
    },
  );
}

interface SQLEditorProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  onFormat?: () => void;
  schema?: SchemaInfo;
  placeholder?: string;
  disabled?: boolean;
  minHeight?: string;
}

function KeyboardHint({ keys, label }: { keys: string[]; label?: string }) {
  return (
    <HStack className="gap-1 items-center">
      {keys.map((key, i) => (
        <span key={i} className="contents">
          {i > 0 && (
            <span className="text-content-layout-3 text-[10px]">+</span>
          )}
          <kbd className="min-w-[20px] px-1.5 py-0.5 rounded-md bg-surface-layout-3 text-content-layout-2 text-[11px] font-medium border border-border-layout-1 shadow-sm text-center">
            {key}
          </kbd>
        </span>
      ))}
      {label && (
        <Text level="caption" className="text-content-layout-3 ml-1">
          {label}
        </Text>
      )}
    </HStack>
  );
}

export function SQLEditor({
  value,
  onChange,
  onSubmit,
  onFormat,
  schema,
  placeholder = "SELECT * FROM users WHERE...",
  disabled = false,
  minHeight = "12rem",
}: SQLEditorProps) {
  const [isFocused, setIsFocused] = useState(false);

  const handleChange = useCallback(
    (val: string) => {
      onChange(val);
    },
    [onChange],
  );

  const extensions = useMemo(() => {
    const dialect = schema?.dialect === "mysql" ? MySQL : PostgreSQL;

    const sqlConfig: Parameters<typeof sql>[0] = {
      dialect,
      upperCaseKeywords: true,
    };

    if (schema?.tables && Object.keys(schema.tables).length > 0) {
      sqlConfig.schema = schema.tables;
    }

    return [
      sql(sqlConfig),
      editorOverrides,
      autocompletion({
        defaultKeymap: true,
        activateOnTyping: true,
      }),
      keymap.of([
        { key: "Ctrl-Space", run: startCompletion },
        { key: "Ctrl-Shift-Space", run: startCompletion },
        { key: "Tab", run: acceptCompletion },
        {
          key: "Mod-Enter",
          run: () => {
            if (!onSubmit) return false;
            onSubmit();
            return true;
          },
        },
      ]),
      createSQLLinter(schema),
      EditorView.lineWrapping,
      EditorView.updateListener.of((update) => {
        if (update.focusChanged) {
          setIsFocused(update.view.hasFocus);
        }
      }),
    ];
  }, [schema, onSubmit]);

  const lineCount = value.split("\n").length;
  const charCount = value.length;

  return (
    <div className="relative w-full group">
      {/* Editor container with subtle glow effect on focus */}
      <div
        className={`relative transition-all duration-300 ${isFocused ? "scale-[1.002]" : ""}`}
      >
        <CodeMirror
          value={value}
          onChange={handleChange}
          extensions={extensions}
          placeholder={placeholder}
          editable={!disabled}
          theme={sqlTheme}
          basicSetup={{
            lineNumbers: true,
            highlightActiveLineGutter: true,
            highlightActiveLine: true,
            foldGutter: false,
            dropCursor: true,
            allowMultipleSelections: false,
            indentOnInput: true,
            bracketMatching: true,
            closeBrackets: true,
            autocompletion: true,
            rectangularSelection: false,
            crosshairCursor: false,
            highlightSelectionMatches: true,
            searchKeymap: true,
          }}
          minHeight={minHeight}
        />

        {/* Floating toolbar */}
        <AnimatePresence>
          {value.trim() && (
            <m.div
              initial={{ opacity: 0, y: -5 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -5 }}
              transition={{ duration: 0.15 }}
              className="absolute top-3 right-3 z-10"
            >
              {onFormat && (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div>
                        <Button
                          variant="primary"
                          modifier="ghost"
                          size="small"
                          label="Format"
                          onClick={onFormat}
                          disabled={disabled || !value.trim()}
                        />
                      </div>
                    </TooltipTrigger>
                    <TooltipContent label="Format SQL (Ctrl+Shift+F)" />
                  </Tooltip>
                </TooltipProvider>
              )}
            </m.div>
          )}
        </AnimatePresence>
      </div>

      {/* Bottom status bar */}
      <m.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.1 }}
        className="flex items-center justify-between mt-3 px-1"
      >
        {/* Stats */}
        <HStack className="gap-4 items-center">
          <AnimatePresence mode="wait">
            {value.trim() && (
              <m.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -10 }}
              >
                <HStack className="gap-3 items-center">
                  <HStack className="gap-1.5 items-center">
                    <Icon
                      name="layers"
                      label="Lines"
                      className="w-3 h-3 text-content-layout-3"
                    />
                    <Text
                      level="caption"
                      className="text-content-layout-3 tabular-nums"
                    >
                      {lineCount} line{lineCount !== 1 ? "s" : ""}
                    </Text>
                  </HStack>
                  <div className="w-px h-3 bg-border-layout-1" />
                  <HStack className="gap-1.5 items-center">
                    <Icon
                      name="querypilot"
                      label="Characters"
                      className="w-3 h-3 text-content-layout-3"
                    />
                    <Text
                      level="caption"
                      className="text-content-layout-3 tabular-nums"
                    >
                      {charCount.toLocaleString()} char
                      {charCount !== 1 ? "s" : ""}
                    </Text>
                  </HStack>
                </HStack>
              </m.div>
            )}
          </AnimatePresence>
        </HStack>

        {/* Keyboard shortcuts */}
        <HStack className="gap-4 items-center">
          <div className="opacity-60 group-hover:opacity-100 transition-opacity">
            <KeyboardHint keys={["Ctrl", "Space"]} label="autocomplete" />
          </div>
          {/*{onSubmit && (
            <div className="opacity-60 group-hover:opacity-100 transition-opacity">
              <KeyboardHint keys={['⌘', '↵']} label="run" />
            </div>
          )}*/}
        </HStack>
      </m.div>
    </div>
  );
}
