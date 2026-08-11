import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import viteReact from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { configDefaults } from 'vitest/config'

export default defineConfig({
  server: {
    port: 3001,
    proxy: {
      '/api': {
        target: 'http://localhost:8787',
        changeOrigin: true,
      },
    },
  },
  plugins: [
    tailwindcss(),
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
      // Route-adjacent unit tests are not routes. Ignoring them prevents the
      // production build from scanning every test file and flooding CI with
      // one warning per file before Rollup starts emitting chunks.
      routeFileIgnorePattern: '\\.test\\.',
    }),
    viteReact({
      babel: {
        plugins: ['babel-plugin-react-compiler'],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // Drop console.log/info/debug from the production bundle — the SSE hooks log
  // full request bodies (SQL + target). Marked pure so minification removes
  // them; console.error/warn stay for real diagnostics. In dev (unminified)
  // they remain. [QW3]
  esbuild: {
    pure: ['console.log', 'console.info', 'console.debug'],
  },
  build: {
    rollupOptions: {
      output: {
        // Group the CodeMirror SQL-editor stack into one cacheable chunk so it
        // is fetched once, on-demand, by whichever SQL route the user opens
        // first — instead of being duplicated or riding the eager entry. [T12]
        //
        // React must be carved into its own eager vendor chunk FIRST: otherwise
        // `@uiw/react-codemirror` drags React into the `codemirror` chunk, and
        // because the eager entry needs React, the whole codemirror chunk gets
        // modulepreloaded on every route — the real reason CodeMirror stayed in
        // the eager graph even after the page components were split out. With
        // React in its own chunk, `codemirror` holds only the editor stack and
        // is fetched lazily by the SQL routes that import it. [T12 / Defect D-1]
        manualChunks(id) {
          if (
            /[\\/]node_modules[\\/](react-dom|react|scheduler|react-compiler-runtime)[\\/]/.test(
              id,
            )
          ) {
            return 'react-vendor'
          }
          // The SQL syntax-highlight palette is the single source of truth shared
          // by `sqlTheme.ts` (the CodeMirror editor theme) and `SqlTokens.tsx`
          // (the regex card highlighter rendered inside every QueryCard). Pin it
          // to its own tiny chunk: otherwise Rollup folds it into the `sqlTheme`
          // chunk, and because that chunk statically imports the CodeMirror stack,
          // QueryCard would transitively pull all of CodeMirror into its graph on
          // every card route — the exact eager-weight defect T12/D-1 fixed. Keep
          // this rule ABOVE the codemirror match. [PS5 item 3 / bundle guard]
          if (id.includes('sqlTokenColors')) {
            return 'sql-token-colors'
          }
          if (
            id.includes('/@codemirror/') ||
            id.includes('/@lezer/') ||
            id.includes('/@uiw/')
          ) {
            return 'codemirror'
          }
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
