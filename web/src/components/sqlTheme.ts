import { createTheme } from "@uiw/codemirror-themes";
import { tags as t } from "@lezer/highlight";
import { EditorView } from "@codemirror/view";

export const sqlTheme = createTheme({
  theme: "dark",
  settings: {
    background: "transparent",
    foreground: "#e8e8e8",
    caret: "#e8e8e8",
    selection: "rgba(99, 102, 241, 0.3)",
    selectionMatch: "rgba(99, 102, 241, 0.15)",
    lineHighlight: "rgba(255, 255, 255, 0.03)",
    gutterBackground: "transparent",
    gutterForeground: "#525866",
    gutterActiveForeground: "#8da1b9",
    gutterBorder: "transparent",
    fontFamily:
      "'JetBrains Mono', 'Fira Code', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  },
  styles: [
    // Comments
    { tag: t.comment, color: "#6b7280", fontStyle: "italic" },
    { tag: t.lineComment, color: "#6b7280", fontStyle: "italic" },
    { tag: t.blockComment, color: "#6b7280", fontStyle: "italic" },
    // Keywords (SELECT, FROM, WHERE, etc.)
    { tag: t.keyword, color: "#f472b6", fontWeight: "500" },
    { tag: t.operatorKeyword, color: "#f472b6", fontWeight: "500" },
    // Operators
    { tag: t.operator, color: "#94a3b8" },
    // Strings (single quotes)
    { tag: t.string, color: "#86efac" },
    // Numbers and booleans
    { tag: t.number, color: "#fcd34d" },
    { tag: t.bool, color: "#fcd34d" },
    { tag: t.null, color: "#fb923c" },
    // Variables and properties
    { tag: t.variableName, color: "#67e8f9" },
    { tag: t.propertyName, color: "#67e8f9" },
    { tag: t.definition(t.variableName), color: "#67e8f9" },
    // Functions
    { tag: t.function(t.variableName), color: "#c4b5fd" },
    // Types
    { tag: t.typeName, color: "#5eead4" },
    { tag: t.className, color: "#5eead4" },
    // Punctuation and brackets
    { tag: t.punctuation, color: "#64748b" },
    { tag: t.bracket, color: "#94a3b8" },
    { tag: t.paren, color: "#94a3b8" },
    { tag: t.squareBracket, color: "#94a3b8" },
    { tag: t.angleBracket, color: "#94a3b8" },
    // Quoted identifiers (double quotes) - light blue to distinguish from keywords
    { tag: t.special(t.string), color: "#93c5fd" },
    // Regex
    { tag: t.regexp, color: "#c4b5fd" },
    // Errors
    { tag: t.deleted, color: "#f87171" },
    { tag: t.invalid, color: "#f87171", textDecoration: "underline" },
  ],
});

export const editorOverrides = EditorView.theme(
  {
    "&": {
      overflow: "hidden",
      borderRadius: "12px",
      border: "1px solid var(--color-border-layout-1)",
      backgroundColor: "var(--color-surface-layout-2)",
      transition: "border-color 0.2s ease, box-shadow 0.2s ease",
    },
    "&.cm-focused": {
      outline: "none",
      borderColor: "var(--color-border-primary-soft)",
      boxShadow:
        "0 0 0 3px rgba(99, 102, 241, 0.1), 0 4px 12px rgba(0, 0, 0, 0.15)",
    },
    ".cm-gutters": {
      backgroundColor: "transparent",
      borderRight: "1px solid var(--color-border-layout-1)",
      paddingRight: "8px",
    },
    ".cm-gutter.cm-lineNumbers": {
      minWidth: "3rem",
    },
    ".cm-activeLineGutter": {
      backgroundColor: "transparent",
    },

    ".cm-placeholder": {
      color: "var(--color-content-layout-3)",
      fontStyle: "italic",
    },

    ".cm-completionIcon": {
      width: "auto",
      marginRight: "8px",
      opacity: 0.7,
    },
  },
  { dark: true },
);

// Minimal styling for readonly display
export const readonlyOverrides = EditorView.theme(
  {
    "&": {
      fontSize: "0.875rem",
    },
    "&.cm-focused": {
      outline: "none",
    },
    ".cm-scroller": {
      overflow: "auto",
    },
    ".cm-content": {
      padding: "0",
    },
    ".cm-line": {
      padding: "0",
    },
    ".cm-gutters": {
      display: "none",
    },
    ".cm-activeLine": {
      backgroundColor: "transparent",
    },
    ".cm-cursor": {
      display: "none",
    },
  },
  { dark: true },
);
