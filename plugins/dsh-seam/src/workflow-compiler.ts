import { readFile } from 'node:fs/promises'
import { checkOperatorWorkflow } from './preflight.js'
import type { Workflow } from './types.js'

export async function compileWorkflow(workflow: Workflow, containerBackend = false): Promise<string> {
  checkOperatorWorkflow(workflow, { containerBackend })
  const bundle = await readFile(new URL('../dist/fixed-script.bundle.js', import.meta.url), 'utf8')
  return `${bundle}\nreturn await SeamFixedWorkflow.run(args, agent, phase);`
}
