import path from 'node:path'
import babel from '@rolldown/plugin-babel'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import viteReact, { reactCompilerPreset } from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { configDefaults } from 'vitest/config'
import { thirdPartyRecord } from './scripts/third-party.mjs'

export default defineConfig({
  server: {
    port: 3001,
    proxy: {
      '/api': {
        target: `http://localhost:${process.env.RDST_API_PORT ?? '8787'}`,
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
      // one warning per file before the bundler starts emitting chunks.
      routeFileIgnorePattern: '\\.test\\.',
    }),
    viteReact(),
    babel({ presets: [reactCompilerPreset()] }),
    thirdPartyRecord(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  build: {
    rolldownOptions: {
      // Drop console.log/info/debug from the production bundle: the SSE hooks
      // log full request bodies (SQL + target). Marked pure so tree-shaking
      // removes them; console.error/warn stay for real diagnostics. In dev
      // (unbundled) they remain. [QW3]
      treeshake: {
        manualPureFunctions: ['console.log', 'console.info', 'console.debug'],
      },
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
        // Groups claim modules in declaration order, and a group also captures
        // the dependencies of its modules that no earlier group claimed.
        codeSplitting: {
          groups: [
            {
              name: 'react-vendor',
              test: /[\\/]node_modules[\\/](react-dom|react|scheduler|react-compiler-runtime)[\\/]/,
            },
            // The SQL syntax-highlight palette is the single source of truth shared
            // by `sqlTheme.ts` (the CodeMirror editor theme) and `SqlTokens.tsx`
            // (the regex card highlighter rendered inside every QueryCard). Pin it
            // to its own tiny chunk: otherwise the bundler folds it into the
            // `sqlTheme` chunk, and because that chunk statically imports the
            // CodeMirror stack, QueryCard would transitively pull all of CodeMirror
            // into its graph on every card route, the exact eager-weight defect
            // T12/D-1 fixed. Keep this group ABOVE the codemirror group. [PS5 item 3
            // / bundle guard]
            { name: 'sql-token-colors', test: 'sqlTokenColors' },
            { name: 'codemirror', test: /[\\/](@codemirror|@lezer|@uiw)[\\/]/ },
          ],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    hookTimeout: 30000,
    maxWorkers: 4,
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
