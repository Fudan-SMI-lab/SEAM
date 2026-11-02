import { readFile, readdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse } from 'yaml'
import { checkOperatorWorkflow } from '../src/preflight.js'
import type { Workflow } from '../src/types.js'

const directory = fileURLToPath(new URL('../../../src/workflows/', import.meta.url))
const files = process.argv.slice(2)
const targets = files.length ? files.map(file => resolve(file)) : (await readdir(directory)).filter(file => /\.ya?ml$/.test(file)).sort().map(file => resolve(directory, file))
let failures = 0
for (const file of targets) {
  try {
    const workflow = parse(await readFile(file, 'utf8')) as Workflow
    checkOperatorWorkflow(workflow, { containerBackend: (workflow.execution_backend as { mode?: string } | undefined)?.mode === 'container' })
    console.log(JSON.stringify({ file, compatible: true }))
  } catch (error) {
    failures++
    console.log(JSON.stringify({ file, compatible: false, blocker: error instanceof Error ? error.message : String(error) }))
  }
}
console.log(JSON.stringify({ total: targets.length, compatible: targets.length - failures, blocked: failures }))
if (failures) process.exitCode = 1
