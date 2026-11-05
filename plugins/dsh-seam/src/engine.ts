import { createHash } from 'node:crypto'
import { realpath } from 'node:fs/promises'
import { conditionDecision, pythonString, resolve, truth } from './expressions.js'
import { failureEvidence } from './evidence.js'
import { executeWorkflowHooks } from './hooks.js'
import { RunStore } from './store.js'
import type { Data, Execution, Handler, Loop, Phase, Result, RunSnapshot, Runtime, Scope, Workflow } from './types.js'

export interface EngineOptions {
  workflow: Workflow
  runtime: Runtime
  handlers: Record<string, Handler>
  store: RunStore
  workspace: string
  context: Data
  validate?: (execution: Execution, result: Result) => Promise<Result>
  defaultTimeoutSeconds: number
  signal?: AbortSignal
  outputDirectory?: string
  containerBackend?: boolean
}

function dispatchRoutes(phase: Phase): Record<string, string> {
  for (const candidate of [phase.routes, phase.params?.routes, phase.transitions]) {
    if (candidate && typeof candidate === 'object' && !Array.isArray(candidate) && Object.keys(candidate).length) return candidate as Record<string, string>
  }
  return {}
}

export function checkWorkflow(workflow: Workflow, handlers: Record<string, Handler>, options: { containerBackend?: boolean } = {}): void {
  if (!workflow.name || !workflow.phases?.length) throw new Error('Workflow needs a name and phases')
  const checkFields = (value: object, allowed: string[], location: string): void => {
    for (const field of Object.keys(value)) if (!allowed.includes(field)) throw new Error(`Not migrated or unknown field: ${location}.${field}`)
  }
  checkFields(workflow, ['name', 'version', 'description', 'target_platform', 'globals', 'agents', 'terminals', 'phases', 'sub_workflows', 'hooks', 'execution_backend', 'rule_migration', 'experience'], 'workflow')
  if (workflow.experience && ((workflow.experience as Data).enabled !== false || (workflow.experience as Data).phase7_enabled === true)) throw new Error('Not migrated: workflow.experience')
  for (const [name, agent] of Object.entries(workflow.agents ?? {})) {
    checkFields(agent, ['role', 'lifecycle', 'runtime_skills'], `agents.${name}`)
    if (agent.lifecycle && !['persistent', 'ephemeral'].includes(agent.lifecycle)) throw new Error(`Unsupported agent lifecycle: ${name}`)
  }
  const backend = workflow.execution_backend as Data | undefined
  if (backend && Object.keys(backend).length && backend.mode !== 'local' && !(backend.mode === 'container' && options.containerBackend)) throw new Error(`Not migrated: workflow.execution_backend.mode=${String(backend.mode ?? 'local')}`)
  if (workflow.rule_migration && Object.keys(workflow.rule_migration as object).length) throw new Error('Not migrated: workflow.rule_migration')
  const allowedHookPoints = new Set(['workflow_start', 'workflow_end'])
  for (const [point, hooks] of Object.entries(workflow.hooks ?? {})) {
    if (!allowedHookPoints.has(point) || !Array.isArray(hooks)) throw new Error(`Unsupported workflow hook point: ${point}`)
    for (const hook of hooks) {
      checkFields(hook, ['type', 'operation', 'save_as', 'critical', 'params'], `hooks.${point}`)
      if ((hook.type ?? 'builtin') !== 'builtin') throw new Error(`Unsupported hook type at ${point}`)
      if (!['snapshot_project', 'copy_artifacts', 'write_summary', 'save_telemetry', 'noop'].includes(hook.operation)) throw new Error(`Unsupported hook operation: ${hook.operation}`)
      if (hook.params !== undefined && (!hook.params || typeof hook.params !== 'object' || Array.isArray(hook.params))) throw new Error(`Invalid hook params: ${hook.operation}`)
    }
  }
  const terminals = new Set(Array.isArray(workflow.terminals) ? workflow.terminals : Object.keys(workflow.terminals ?? {}))
  const validate = (phases: Phase[], top: boolean): void => {
    const ids = new Set(phases.map(phase => phase.id))
    if (ids.size !== phases.length) throw new Error('Duplicate phase ids')
    for (const phase of phases) {
      if (!phase.id) throw new Error('Missing phase id')
      checkFields(phase, ['id', 'name', 'type', 'agent', 'operation', 'handler', 'prompt_template', 'validator', 'timeout', 'condition', 'input_mapping', 'input_schema', 'params', 'output_as', 'sub_workflow', 'on_failure', 'transitions', 'runtime_skills', 'route_field', 'routes', 'output_schema', 'command', 'cwd', 'env', 'capture'], `phases.${phase.id}`)
      const type = phase.type ?? 'llm'
      if (!['llm', 'loop', 'dispatch'].includes(type) && !handlers[type]) throw new Error(`No handler for phase type ${type}`)
      if (phase.hooks || phase.retrieve_experience || phase.validate_only) throw new Error(`Not migrated: phase extensions in ${phase.id}`)
      if (type === 'llm' && (!phase.agent || !workflow.agents?.[phase.agent])) throw new Error(`Unknown agent in ${phase.id}`)
      if (type === 'loop' && !workflow.sub_workflows?.[phase.sub_workflow ?? String(phase.params?.sub_workflow ?? '')]) throw new Error(`Unknown sub-workflow in ${phase.id}`)
      if (phase.timeout !== undefined && (!Number.isFinite(phase.timeout) || phase.timeout <= 0)) throw new Error(`Invalid timeout in ${phase.id}`)
      if (phase.on_failure && !['continue', 'break'].includes(phase.on_failure)) throw new Error(`Unsupported on_failure in ${phase.id}`)
      if (type === 'loop' && phase.timeout !== undefined) throw new Error(`Not migrated: loop-wide deadline in ${phase.id}`)
      if (!top && type !== 'dispatch' && Object.keys(phase.transitions ?? {}).length) throw new Error(`Not migrated: nested transitions in ${phase.id}`)
      for (const target of Object.values(phase.transitions ?? {})) {
        if (top && !ids.has(target) && !terminals.has(target)) throw new Error(`Unknown transition ${target}`)
      }
      if (type === 'dispatch') {
        const routes = dispatchRoutes(phase)
        if (!Object.keys(routes).length) throw new Error(`Missing dispatch routes in ${phase.id}`)
        for (const target of Object.values(routes)) if (!ids.has(target)) throw new Error(`Unknown dispatch target ${target}`)
      }
    }
  }
  validate(workflow.phases, true)
  for (const loop of Object.values(workflow.sub_workflows ?? {})) {
    checkFields(loop, ['id', 'type', 'max_iterations', 'stagnation_threshold', 'review_gate_enabled', 'stop_conditions', 'phases', 'blocks'], 'sub_workflows')
    for (const stop of loop.stop_conditions ?? []) if (!['success', 'failure', 'stagnation'].includes(stop.status)) throw new Error(`Unsupported loop stop status: ${stop.status}`)
    if (typeof workflow.globals?.review_gate_enabled === 'boolean' ? workflow.globals.review_gate_enabled : loop.review_gate_enabled === true) throw new Error('Not migrated: review gate policy')
    if (Object.keys(loop.blocks ?? {}).length) throw new Error('Not migrated: sub-workflow blocks')
    validate(loop.phases, false)
    for (const block of Object.values(loop.blocks ?? {})) validate(block.phases ?? [], false)
  }
}

export class WorkflowEngine {
  private readonly cancellation = new AbortController()
  private snapshot!: RunSnapshot
  private started = false
  private readonly scope: Scope
  constructor(private readonly options: EngineOptions) {
    checkWorkflow(options.workflow, options.handlers, { containerBackend: options.containerBackend })
    if (!(options.defaultTimeoutSeconds > 0 && Number.isFinite(options.defaultTimeoutSeconds))) throw new Error('An explicit finite phase timeout is required')
    this.scope = { state: {}, context: structuredClone(options.context), globals: structuredClone(options.workflow.globals ?? {}), loop_vars: {}, loop_state: {}, loop_history: [], step_outputs: {} }
  }
  cancel(): void { this.cancellation.abort(new Error('Workflow cancelled')) }
  status(): RunSnapshot | undefined { return this.snapshot && structuredClone(this.snapshot) }
  async checkpoint(): Promise<void> { if (this.snapshot) await this.persist() }
  private async persist(): Promise<void> {
    this.snapshot.state = structuredClone(this.scope.state)
    this.snapshot.sessions = this.options.runtime.sessions()
    await this.options.store.snapshot(this.snapshot)
  }
  async run(): Promise<RunSnapshot> {
    if (this.started) throw new Error('An engine can run only once')
    this.started = true
    const workspace = await realpath(this.options.workspace)
    await this.options.store.acquire()
    const { workflow } = this.options
    this.snapshot = { format: 1, runId: this.options.store.runId, workflow: workflow.name, workflowHash: createHash('sha256').update(JSON.stringify(workflow)).digest('hex'), workspace, status: 'running', state: {}, context: this.scope.context, sessions: {} }
    let next: string | undefined = workflow.phases[0]!.id
    const terminals = new Set(Array.isArray(workflow.terminals) ? workflow.terminals : Object.keys(workflow.terminals ?? {}))
    try {
      await this.persist()
      await this.runHooks('workflow_start')
      while (next && !terminals.has(next)) {
        this.signal().throwIfAborted()
        const index = workflow.phases.findIndex(phase => phase.id === next)
        const phase = workflow.phases[index]
        if (!phase) throw new Error(`Unknown phase ${next}`)
        this.snapshot.currentPhase = phase.id
        await this.persist()
        const result = await this.execute(phase, this.scope)
        this.scope.state[phase.id] = result.output
        if (phase.output_as) this.scope.state[phase.output_as] = result.output
        await this.persist()
        const transitions = phase.transitions ?? {}
        next = result.next ?? transitions[`on_${result.status === 'skipped' ? 'skip' : result.status}`] ?? transitions[result.status]
        if (!next) {
          if (!['success', 'skipped'].includes(result.status)) { this.snapshot.status = 'failed'; break }
          next = workflow.phases[index + 1]?.id
        }
      }
      if (this.snapshot.status === 'running') {
        this.snapshot.terminal = next
        this.snapshot.status = next === 'failed' ? 'failed' : next === 'blocked' ? 'blocked' : 'complete'
      }
    } catch (error) {
      this.snapshot.status = this.signal().aborted ? 'cancelled' : 'failed'
      this.snapshot.error = error instanceof Error ? error.message : String(error)
    } finally {
      try { await this.runHooks('workflow_end') }
      catch (error) { this.snapshot.status = 'failed'; this.snapshot.error = `Critical workflow_end hook failed: ${String(error)}` }
      try { await this.options.runtime.dispose() }
      catch (error) { this.snapshot.status = 'failed'; this.snapshot.error = `Runtime cleanup failed: ${String(error)}` }
      try { await this.persist() } finally { await this.options.store.release() }
    }
    return structuredClone(this.snapshot)
  }
  private async runHooks(point: 'workflow_start' | 'workflow_end'): Promise<void> {
    const hooks = this.options.workflow.hooks?.[point] ?? []
    if (!hooks.length) return
    const context: Data = { ...this.scope.context, state: this.scope.state, phase_results: this.scope.state, status: this.snapshot.status, output_dir: this.options.outputDirectory ?? this.options.store.directory, _hook_point: point }
    const results = await executeWorkflowHooks(hooks, context, this.options.outputDirectory ?? this.options.store.directory, this.options.workspace)
    this.scope.state.hooks = { ...((this.scope.state.hooks as Data | undefined) ?? {}), [point]: results }
    await this.options.store.artifact(`hooks-${point}`, results)
    await this.persist()
  }
  private signal(): AbortSignal {
    return this.options.signal ? AbortSignal.any([this.cancellation.signal, this.options.signal]) : this.cancellation.signal
  }
  private async execute(phase: Phase, scope: Scope): Promise<Result> {
    this.signal().throwIfAborted()
    const input = resolve(phase.input_mapping ?? {}, scope)
    const decision = phase.condition ? conditionDecision(phase.condition, scope) : { matched: true }
    if (decision.error) await this.options.store.artifact(`${phase.id}-condition`, { condition: phase.condition, ...decision })
    const result = !decision.matched ? { status: 'skipped' as const, output: {} } : phase.type === 'loop' ? await this.loop(phase, scope) : await this.step(phase, scope)
    result.output.framework_execution_status = result.status
    await this.options.store.artifact(phase.id, { phase: phase.id, iteration: scope.loop_state.iteration, input, result })
    await this.persist()
    return result
  }
  private async step(phase: Phase, scope: Scope): Promise<Result> {
    if (phase.type === 'dispatch') {
      const value = resolve(phase.route_field ?? phase.params?.route_field, scope)
      const key = value && typeof value === 'object' && !Array.isArray(value) ? Object.hasOwn(value, 'value') ? (value as Data).value : Object.hasOwn(value, 'role') ? (value as Data).role : '' : value
      const route = pythonString(key)
      const routes = dispatchRoutes(phase)
      const next = Object.hasOwn(routes, route) ? routes[route] : undefined
      if (!next) return { status: 'failure', output: { failure_kind: 'invalid_dispatch_route', route, available_routes: Object.keys(routes) } }
      return { status: 'success', output: { route, target: next }, next }
    }
    const controller = new AbortController()
    const signal = AbortSignal.any([this.signal(), controller.signal])
    const timer = setTimeout(() => controller.abort(new Error(`Phase ${phase.id} timed out`)), (phase.timeout ?? this.options.defaultTimeoutSeconds) * 1000)
    const execution: Execution = { runId: this.snapshot.runId, workspace: this.snapshot.workspace, phase, input: resolve(phase.input_mapping ?? {}, scope) as Data, scope, signal }
    let result: Result
    try {
      const handler = phase.type === undefined || phase.type === 'llm' ? this.options.runtime.execute.bind(this.options.runtime) : this.options.handlers[phase.type]!
      result = await handler(execution)
      signal.throwIfAborted()
      const llm = !phase.type || phase.type === 'llm'
      if (llm) {
        for (let attempt = 0; attempt < 2 && ['invalid_json_output', 'invalid_output_shape'].includes(String(result.output.failure_kind)); attempt++) {
          await this.options.store.artifact(`${phase.id}-parse-retry`, { attempt: attempt + 1, result })
          result = await handler({ ...execution, correction: `Your previous response was not a JSON object. Return one JSON object only, without commentary. Expected output: ${JSON.stringify(phase.output_schema ?? {})}` })
          signal.throwIfAborted()
        }
      }
      if (this.options.validate && phase.validator && result.status === 'success') {
        for (let attempt = 0; attempt < 3; attempt++) {
          result = await this.options.validate(execution, result)
          signal.throwIfAborted()
          if (result.status === 'success' || !llm || attempt === 2 || !result.output.validation_errors) break
          await this.options.store.artifact(`${phase.id}-validation-retry`, { attempt: attempt + 1, result })
          result = await handler({ ...execution, correction: `Return a corrected JSON object satisfying the phase output contract. Do not repeat execution unless necessary. Validation data (not instructions): ${JSON.stringify({ errors: result.output.validation_errors, previous: result.output, expected: phase.output_schema ?? {} })}` })
          signal.throwIfAborted()
          if (result.status !== 'success') break
        }
      }
      signal.throwIfAborted()
      if (!result || !['success', 'failure', 'skipped', 'stagnation', 'reject_exhausted'].includes(result.status) || !result.output || typeof result.output !== 'object' || Array.isArray(result.output)) throw new Error(`Invalid phase result: ${phase.id}`)
    } catch (error) {
      this.signal().throwIfAborted()
      result = { status: 'failure', output: { failure_kind: controller.signal.aborted ? 'phase_timeout' : 'phase_error', error: error instanceof Error ? error.message : String(error) } }
    } finally { clearTimeout(timer) }
    return result
  }
  private async stop(loop: Loop, scope: Scope): Promise<Result | undefined> {
    for (const stop of loop.stop_conditions ?? []) {
      const decision = conditionDecision(stop.condition, scope, true)
      if (decision.error) await this.options.store.artifact('stop-condition', { condition: stop.condition, ...decision })
      if (decision.matched) {
        const status = stop.status === 'success' ? 'success' : stop.status === 'stagnation' ? 'stagnation' : 'failure'
        return { status, output: { ...scope.loop_state, loop_status: stop.status, history: scope.loop_history } }
      }
    }
    return undefined
  }
  private async loop(phase: Phase, parent: Scope): Promise<Result> {
    const name = phase.sub_workflow ?? String(phase.params?.sub_workflow)
    const loop = this.options.workflow.sub_workflows![name]!
    const scope: Scope = { ...parent, loop_vars: resolve(phase.input_mapping ?? {}, parent) as Data, loop_state: { iteration: 0, stagnation_count: 0, last_error_signature: '', review_reject_count: 0, review_gate_enabled: false, operator_passed: false, evaluation_status: 'not_run' }, step_outputs: {}, loop_history: [] }
    const maximum = Number(resolve(truth(parent.globals.max_repair_iterations) ? parent.globals.max_repair_iterations : loop.max_iterations ?? 10, scope))
    const threshold = typeof loop.stagnation_threshold === 'number' ? Math.trunc(loop.stagnation_threshold) : 3
    if (!Number.isSafeInteger(maximum) || maximum < 1 || !Number.isSafeInteger(threshold) || threshold < 1) throw new Error(`Invalid loop limits: ${name}`)
    for (let iteration = 1; iteration <= maximum; iteration++) {
      scope.loop_state.iteration = iteration
      scope.step_outputs = {}
      const dispatchTargets = new Set(loop.phases.filter(step => step.type === 'dispatch').flatMap(step => Object.values(dispatchRoutes(step))))
      let selected: string | undefined
      let iterationStatus: Result['status'] = 'success'
      for (const step of loop.phases) {
        if (dispatchTargets.has(step.id) && selected !== step.id) continue
        const result = await this.execute(step, scope)
        if (result.status === 'skipped') continue
        const alias = step.output_as ?? step.id
        scope.step_outputs[step.id] = result.output
        scope.step_outputs[alias] = result.output
        scope.loop_state[step.id] = result.output
        scope.loop_state[alias] = result.output
        scope.state[alias] = result.output
        if (step.type === 'dispatch') {
          selected = result.next
          if (result.status === 'failure') scope.step_outputs.dispatch_error = result.output
        }
        if (step.id === 'prepare_operator') {
          scope.loop_state.prepare_attempted = true
          scope.step_outputs.prepare_attempted = true
        }
        const fields = step.id === 'evaluate_operator' ? ['operator_passed', 'evaluation_status', 'operator_pass_rate', 'workload_pass_rate', 'total_workloads', 'passed_workloads', 'failure_category', 'timeout_classification', 'cleanup_status', 'visible_device', 'failure_manifest_path', 'candidate_manifest_path', 'candidate_manifest', 'evaluation_scope', 'evaluation_completeness', 'first_failure_workload', 'cascade_failure_count', 'resource_before', 'resource_after', 'work_dir', 'profile_dir', 'traces_path', 'summary_path', 'stdout_path', 'stderr_path'] : step.type === 'builtin' && result.output.operation === 'operator_kernelbench_eval' ? ['operator_passed', 'evaluation_status', 'workload_pass_rate', 'operator_pass_rate'] : []
        for (const field of fields) {
          if (Object.hasOwn(result.output, field)) {
            scope.loop_state[field] = result.output[field]
            scope.step_outputs[field] = result.output[field]
          }
        }
        iterationStatus = result.status
        if (result.status === 'failure' && (step.on_failure === 'break' || step.type === 'dispatch' || result.output.validation_errors)) break
      }
      scope.loop_history.push({ iteration, status: iterationStatus, outputs: structuredClone(scope.step_outputs) })
      await this.options.store.artifact(`${phase.id}-iteration`, { ...scope.loop_state, history: scope.loop_history })
      if (iterationStatus === 'failure' && scope.step_outputs.dispatch_error) return { status: 'failure', output: { ...scope.loop_state, history: scope.loop_history } }
      const stop = await this.stop(loop, scope)
      if (stop) return stop
      const signature = failureEvidence(scope.loop_state).split('\n').map(line => line.trimEnd()).join('\n')
      const previous = scope.loop_state.last_error_signature
      scope.loop_state.stagnation_count = signature && signature === previous ? Number(scope.loop_state.stagnation_count) + 1 : 1
      scope.loop_state.last_error_signature = signature
      if (Number(scope.loop_state.stagnation_count) >= threshold) return { status: 'stagnation', output: { ...scope.loop_state, history: scope.loop_history } }
      if (iterationStatus === 'failure' && (!scope.loop_vars.OPERATOR_TASK_DIR || iteration === maximum)) return { status: 'failure', output: { ...scope.loop_state, history: scope.loop_history } }
    }
    return { status: 'failure', output: { ...scope.loop_state, loop_status: 'max_iterations', history: scope.loop_history } }
  }
}
