import { build } from 'esbuild'

await build({ entryPoints: [new URL('../src/fixed-script.ts', import.meta.url).pathname], outfile: new URL('../dist/fixed-script.bundle.js', import.meta.url).pathname, bundle: true, platform: 'neutral', format: 'iife', globalName: 'SeamFixedWorkflow', target: 'es2023' })
