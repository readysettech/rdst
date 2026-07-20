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
    tanstackRouter({ target: 'react', autoCodeSplitting: true }),
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
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
