import { conditionDecision, pythonString, resolve, truth } from './expressions.js'
import { failureEvidence } from './evidence.js'
import type { Data, Loop, Phase, Result, Scope, Workflow } from './types.js'

interface Input { workflow: Workflow; context: Data; token: string }
type AgentCall = (prompt: string, options: { label: string }) => Promise<unknown>

export async function run(input: Input, agent: AgentCall, phaseEvent: (title: string) => void): Promise<Data> {
  const workflow = input.workflow
  let currentPhase = ''
  const scope: Scope = { state: {}, context: input.context, globals: workflow.globals ?? {}, loop_vars: {}, loop_state: {}, loop_history: [], step_outputs: {} }
  const rpc = async (action: string, payload: Data): Promise<Data> => {
    const response = await agent(JSON.stringify({ version: 1, token: input.token, action, ...payload }), { label: `seam:${action}` })
    if (typeof response !== 'string') throw new Error('SEAM provider did not return JSON text')
    const envelope = JSON.parse(response)
    if (envelope?.version !== 1 || envelope.ok !== true || !envelope.value || typeof envelope.value !== 'object' || Array.isArray(envelope.value)) throw new Error('Invalid SEAM provider response')
    return envelope.value
  }
  const artifact = async (name: string, value: unknown): Promise<void> => { await rpc('artifact', { name, value }) }
  const checkpoint = async (id: string, current: Scope): Promise<void> => { currentPhase = id; await rpc('checkpoint', { currentPhase, state: current.state }) }
  const routes = (step: Phase): Record<string, string> => {
    for (const candidate of [step.routes, step.params?.routes, step.transitions]) {
      if (candidate && typeof candidate === 'object' && !Array.isArray(candidate) && Object.keys(candidate).length) return candidate as Record<string, string>
    }
    return {}
  }
  const execute = async (step: Phase, current: Scope): Promise<Result> => {
    phaseEvent(step.name ?? step.id)
    await checkpoint(step.id, current)
    const mapped = resolve(step.input_mapping ?? {}, current) as Data
    const decision = step.condition ? conditionDecision(step.condition, current) : { matched: true }
    if (decision.error) await artifact(`${step.id}-condition`, { condition: step.condition, ...decision })
    let result: Result
    if (!decision.matched) result = { status: 'skipped', output: {} }
    else if (step.type === 'loop') result = await loop(step, current)
    else if (step.type === 'dispatch') {
      const value = resolve(step.route_field ?? step.params?.route_field, current)
      const key = value && typeof value === 'object' && !Array.isArray(value) ? Object.hasOwn(value, 'value') ? (value as Data).value : Object.hasOwn(value, 'role') ? (value as Data).role : '' : value
      const route = pythonString(key)
      const choices = routes(step)
      const next = Object.hasOwn(choices, route) ? choices[route] : undefined
      result = next ? { status: 'success', output: { route, target: next }, next } : { status: 'failure', output: { failure_kind: 'invalid_dispatch_route', route, available_routes: Object.keys(choices) } }
    } else result = await rpc('step', { phaseId: step.id, input: mapped, scope: current }) as unknown as Result
    result.output.framework_execution_status = result.status
    await artifact(step.id, { phase: step.id, iteration: current.loop_state.iteration, input: mapped, result })
    return result
  }
  const stop = async (definition: Loop, current: Scope): Promise<Result | undefined> => {
    for (const condition of definition.stop_conditions ?? []) {
      const decision = conditionDecision(condition.condition, current, true)
      if (decision.error) await artifact('stop-condition', { condition: condition.condition, ...decision })
      if (decision.matched) return { status: condition.status === 'success' ? 'success' : condition.status === 'stagnation' ? 'stagnation' : 'failure', output: { ...current.loop_state, loop_status: condition.status, history: current.loop_history } }
    }
    return undefined
  }
  const loop = async (step: Phase, parent: Scope): Promise<Result> => {
    const name = step.sub_workflow ?? String(step.params?.sub_workflow)
    const definition = workflow.sub_workflows![name]!
    const current: Scope = { ...parent, loop_vars: resolve(step.input_mapping ?? {}, parent) as Data, loop_state: { iteration: 0, stagnation_count: 0, last_error_signature: '', review_reject_count: 0, review_gate_enabled: false, operator_passed: false, evaluation_status: 'not_run' }, step_outputs: {}, loop_history: [] }
    const maximum = Number(resolve(truth(parent.globals.max_repair_iterations) ? parent.globals.max_repair_iterations : definition.max_iterations ?? 10, current))
    const threshold = typeof definition.stagnation_threshold === 'number' ? Math.trunc(definition.stagnation_threshold) : 3
    if (!Number.isSafeInteger(maximum) || maximum < 1 || !Number.isSafeInteger(threshold) || threshold < 1) throw new Error(`Invalid loop limits: ${name}`)
    for (let iteration = 1; iteration <= maximum; iteration++) {
      current.loop_state.iteration = iteration
      current.step_outputs = {}
      const targets = new Set(definition.phases.filter(item => item.type === 'dispatch').flatMap(item => Object.values(routes(item))))
      let selected: string | undefined
      let iterationStatus: Result['status'] = 'success'
      for (const item of definition.phases) {
        if (targets.has(item.id) && selected !== item.id) continue
        const result = await execute(item, current)
        if (result.status === 'skipped') continue
        const alias = item.output_as ?? item.id
        current.step_outputs[item.id] = result.output
        current.step_outputs[alias] = result.output
        current.loop_state[item.id] = result.output
        current.loop_state[alias] = result.output
        current.state[alias] = result.output
        if (item.type === 'dispatch') {
          selected = result.next
          if (result.status === 'failure') current.step_outputs.dispatch_error = result.output
        }
        if (item.id === 'prepare_operator') {
          current.loop_state.prepare_attempted = true
          current.step_outputs.prepare_attempted = true
        }
        const fields = item.id === 'evaluate_operator' ? ['operator_passed', 'evaluation_status', 'operator_pass_rate', 'workload_pass_rate', 'total_workloads', 'passed_workloads', 'failure_category', 'timeout_classification', 'cleanup_status', 'visible_device', 'failure_manifest_path', 'candidate_manifest_path', 'candidate_manifest', 'evaluation_scope', 'evaluation_completeness', 'first_failure_workload', 'cascade_failure_count', 'resource_before', 'resource_after', 'work_dir', 'profile_dir', 'traces_path', 'summary_path', 'stdout_path', 'stderr_path'] : item.type === 'builtin' && result.output.operation === 'operator_kernelbench_eval' ? ['operator_passed', 'evaluation_status', 'workload_pass_rate', 'operator_pass_rate'] : []
        for (const field of fields) if (Object.hasOwn(result.output, field)) {
          current.loop_state[field] = result.output[field]
          current.step_outputs[field] = result.output[field]
        }
        iterationStatus = result.status
        if (result.status === 'failure' && (item.on_failure === 'break' || item.type === 'dispatch' || result.output.validation_errors)) break
      }
      current.loop_history.push({ iteration, status: iterationStatus, outputs: JSON.parse(JSON.stringify(current.step_outputs)) })
      await artifact(`${step.id}-iteration`, { ...current.loop_state, history: current.loop_history })
      await checkpoint(step.id, current)
      if (iterationStatus === 'failure' && current.step_outputs.dispatch_error) return { status: 'failure', output: { ...current.loop_state, history: current.loop_history } }
      const stopped = await stop(definition, current)
      if (stopped) return stopped
      const signature = failureEvidence(current.loop_state).split('\n').map(line => line.trimEnd()).join('\n')
      current.loop_state.stagnation_count = signature && signature === current.loop_state.last_error_signature ? Number(current.loop_state.stagnation_count) + 1 : 1
      current.loop_state.last_error_signature = signature
      if (Number(current.loop_state.stagnation_count) >= threshold) return { status: 'stagnation', output: { ...current.loop_state, history: current.loop_history } }
      if (iterationStatus === 'failure' && (!current.loop_vars.OPERATOR_TASK_DIR || iteration === maximum)) return { status: 'failure', output: { ...current.loop_state, history: current.loop_history } }
    }
    return { status: 'failure', output: { ...current.loop_state, loop_status: 'max_iterations', history: current.loop_history } }
  }
  let next = workflow.phases[0]?.id
  let status = 'running'
  const terminals = new Set(Array.isArray(workflow.terminals) ? workflow.terminals : Object.keys(workflow.terminals ?? {}))
  try {
    const started = await rpc('hooks', { point: 'workflow_start', state: scope.state, status })
    scope.state = started.state as Data
    while (next && !terminals.has(next)) {
      const index = workflow.phases.findIndex(item => item.id === next)
      const current = workflow.phases[index]
      if (!current) throw new Error(`Unknown phase ${next}`)
      const result = await execute(current, scope)
      scope.state[current.id] = result.output
      if (current.output_as) scope.state[current.output_as] = result.output
      await checkpoint(current.id, scope)
      const transitions = current.transitions ?? {}
      next = result.next ?? transitions[`on_${result.status === 'skipped' ? 'skip' : result.status}`] ?? transitions[result.status]
      if (!next) {
        if (!['success', 'skipped'].includes(result.status)) { status = 'failed'; break }
        next = workflow.phases[index + 1]?.id
      }
    }
    if (status === 'running') status = next === 'failed' ? 'failed' : next === 'blocked' ? 'blocked' : 'complete'
    return { status, terminal: next ?? null, state: scope.state }
  } finally { await checkpoint(currentPhase, scope) }
}
