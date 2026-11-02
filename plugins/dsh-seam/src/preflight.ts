import { operatorOperations, supportedValidators } from './bridge.js'
import { checkWorkflow } from './engine.js'
import { shellPhase } from './shell.js'
import type { Workflow } from './types.js'

export function checkOperatorWorkflow(workflow: Workflow, options: { containerBackend?: boolean } = {}): void {
  checkWorkflow(workflow, { builtin: async () => { throw new Error('Preflight cannot execute builtins') }, shell: shellPhase }, options)
  const phases = [...workflow.phases, ...Object.values(workflow.sub_workflows ?? {}).flatMap(loop => [...loop.phases, ...Object.values(loop.blocks ?? {}).flatMap(block => block.phases ?? [])])]
  if (new Set(phases.map(phase => phase.id)).size !== phases.length) throw new Error('dsh workflow requires globally unique phase ids')
  if (options.containerBackend && phases.some(phase => phase.type === 'builtin')) throw new Error('Container workflows with builtin phases remain blocked until builtin operations use the shared execution backend')
  for (const phase of phases) {
    if (phase.type === 'builtin' && !operatorOperations.has(String(phase.operation ?? phase.params?.operation))) throw new Error(`Builtin not migrated: ${phase.operation ?? phase.params?.operation}`)
    if (phase.validator && !supportedValidators.has(phase.validator)) throw new Error(`Validator not migrated: ${phase.validator}`)
  }
}
