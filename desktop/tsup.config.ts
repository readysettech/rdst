import { defineConfig } from 'tsup'

export default defineConfig([
  {
    entry: { 'main/index': 'src/main/index.ts' },
    outDir: 'out',
    format: 'esm',
    platform: 'node',
    target: 'node22',
    external: ['electron'],
    clean: false,
    sourcemap: true,
  },
  {
    entry: { 'preload/index': 'src/preload/index.ts' },
    outDir: 'out',
    format: 'esm',
    outExtension: () => ({ js: '.mjs' }),
    platform: 'node',
    target: 'node22',
    external: ['electron'],
    clean: false,
    sourcemap: true,
  },
])
