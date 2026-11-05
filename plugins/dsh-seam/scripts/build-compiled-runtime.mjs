import { build } from 'esbuild'

await build({ entryPoints: [new URL('../src/compiled-runtime.ts', import.meta.url).pathname], outfile: new URL('../dist/compiled-runtime.bundle.js', import.meta.url).pathname, bundle: true, platform: 'neutral', format: 'iife', globalName: 'SeamCompiledRuntime', target: 'es2023' })
