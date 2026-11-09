import { readFile } from 'node:fs/promises'
import { checkOperatorWorkflow } from './preflight.js'
import type { Phase, Workflow } from './types.js'

export const compilerVersion = 'seam-static-workflow/1'
export interface CompiledWorkflow {
  script: string
  workflow: Workflow
  stages: Record<string, Phase>
  sourceMap: { phaseId: string; yamlPath: string; startLine: number; endLine: number }[]
}

const json = (value: unknown): string => JSON.stringify(value)
const evaluationFields = ['operator_passed', 'evaluation_status', 'operator_pass_rate', 'workload_pass_rate', 'total_workloads', 'passed_workloads', 'failure_category', 'timeout_classification', 'cleanup_status', 'visible_device', 'failure_manifest_path', 'candidate_manifest_path', 'candidate_manifest', 'evaluation_scope', 'evaluation_completeness', 'first_failure_workload', 'cascade_failure_count', 'resource_before', 'resource_after', 'work_dir', 'profile_dir', 'traces_path', 'summary_path', 'stdout_path', 'stderr_path']

function routes(step: Phase): Record<string, string> {
  for (const candidate of [step.routes, step.params?.routes, step.transitions]) {
    if (candidate && typeof candidate === 'object' && !Array.isArray(candidate) && Object.keys(candidate).length) return candidate as Record<string, string>
  }
  return {}
}

export async function generateWorkflow(definition: Workflow, containerBackend = false): Promise<CompiledWorkflow> {
  const workflow = structuredClone(definition)
  checkOperatorWorkflow(workflow, { containerBackend })
  const all = [...workflow.phases, ...Object.values(workflow.sub_workflows ?? {}).flatMap(loop => loop.phases)]
  const names = new Map(all.map((step, index) => [step.id, `step_${index}`]))
  const stages = Object.fromEntries(all.filter(step => !['loop', 'dispatch'].includes(step.type ?? 'llm')).map(step => [step.id, step]))
  const runtime = await readFile(new URL('../dist/compiled-runtime.bundle.js', import.meta.url), 'utf8')
  const lines = [runtime, `const runtime = SeamCompiledRuntime.createRuntime(args.context, ${json(workflow.globals ?? {})}, args.token, agent, phase, args.outputDir);`, 'const scope = runtime.scope;']
  const sourceMap: CompiledWorkflow['sourceMap'] = []
  const emit = (line: string) => { lines.push(line) }
  const lineCount = () => lines.join('\n').split('\n').length
  const compileStep = (step: Phase, yamlPath: string): void => {
    const startLine = lineCount() + 1
    emit(`async function ${names.get(step.id)}(current) {`)
    emit(`await runtime.enter(${json(step.id)}, ${json(step.name ?? step.id)}, current);`)
    emit(`const mapped = runtime.resolve(${json(step.input_mapping ?? {})}, current);`)
    emit('let result;')
    if (step.condition) emit(`if (!await runtime.matches(${json(step.condition)}, current)) result = {status: 'skipped', output: {}}; else {`)
    if (step.type === 'loop') {
      emit('result = await (async (current) => {')
      emit('const parent = current;')
      emit('current = runtime.loopScope(parent, mapped);')
      const loopName = step.sub_workflow ?? String(step.params?.sub_workflow)
      const definition = workflow.sub_workflows![loopName]!
      emit(`const maximum = Number(runtime.resolve(runtime.truth(parent.globals.max_repair_iterations) ? parent.globals.max_repair_iterations : ${json(definition.max_iterations ?? 10)}, current));`)
      emit(`const threshold = ${typeof definition.stagnation_threshold === 'number' ? Math.trunc(definition.stagnation_threshold) : 3};`)
      emit("if (!Number.isSafeInteger(maximum) || maximum < 1 || !Number.isSafeInteger(threshold) || threshold < 1) throw new Error('Invalid loop limits');")
      emit('for (let iteration = 1; iteration <= maximum; iteration++) {')
      emit('current.loop_state.iteration = iteration; current.step_outputs = {};')
      emit("let selected; let iterationStatus = 'success';")
      emit('round: {')
      const targets = new Set(definition.phases.filter(item => item.type === 'dispatch').flatMap(item => Object.values(routes(item))))
      for (const item of definition.phases) {
        emit(targets.has(item.id) ? `if (selected === ${json(item.id)}) {` : '{')
        emit(`const result = await ${names.get(item.id)}(current);`)
        emit("if (result.status !== 'skipped') {")
        const fields = item.id === 'evaluate_operator' ? json(evaluationFields) : item.type === 'builtin' ? `(result.output.operation === 'operator_kernelbench_eval' ? ['operator_passed', 'evaluation_status', 'workload_pass_rate', 'operator_pass_rate'] : [])` : '[]'
        emit(`runtime.promote(current, ${json(item.id)}, ${json(item.output_as ?? item.id)}, result, ${fields});`)
        if (item.type === 'dispatch') emit("selected = result.next; if (result.status === 'failure') current.step_outputs.dispatch_error = result.output;")
        if (item.id === 'prepare_operator') emit('current.loop_state.prepare_attempted = true; current.step_outputs.prepare_attempted = true;')
        emit('iterationStatus = result.status;')
        emit(`if (result.status === 'failure' && (${item.on_failure === 'break' || item.type === 'dispatch'} || result.output.validation_errors)) break round;`)
        emit('} }')
      }
      emit('}')
      emit('runtime.remember(current, iteration, iterationStatus);')
      emit(`await runtime.artifact(${json(`${step.id}-iteration`)}, {...current.loop_state, history: current.loop_history});`)
      emit(`await runtime.checkpoint(${json(step.id)}, current);`)
      emit("if (iterationStatus === 'failure' && current.step_outputs.dispatch_error) return runtime.loopResult(current, 'failure');")
      for (const stop of definition.stop_conditions ?? []) {
        emit(`if (await runtime.matches(${json(stop.condition)}, current, true)) return runtime.loopResult(current, ${json(stop.status === 'success' ? 'success' : stop.status === 'stagnation' ? 'stagnation' : 'failure')}, ${json(stop.status)});`)
      }
      emit("if (runtime.stagnates(current, threshold)) return runtime.loopResult(current, 'stagnation');")
      emit("if (iterationStatus === 'failure' && (!current.loop_vars.OPERATOR_TASK_DIR || iteration === maximum)) return runtime.loopResult(current, 'failure');")
      emit('}')
      emit("return runtime.loopResult(current, 'failure', 'max_iterations');")
      emit('})(current);')
    } else if (step.type === 'dispatch') {
      emit(`result = runtime.route(runtime.resolve(${json(step.route_field ?? step.params?.route_field ?? null)}, current), ${json(routes(step))});`)
    } else emit(`result = await runtime.leaf(${json(step.id)}, mapped, current);`)
    if (step.condition) emit('}')
    emit(`return await runtime.record(${json(step.id)}, mapped, result, current);`)
    emit('}')
    sourceMap.push({ phaseId: step.id, yamlPath, startLine, endLine: lineCount() })
  }
  workflow.phases.forEach((step, index) => compileStep(step, `phases[${index}]`))
  for (const [name, loop] of Object.entries(workflow.sub_workflows ?? {})) loop.phases.forEach((step, index) => compileStep(step, `sub_workflows.${name}.phases[${index}]`))
  emit(`let next = ${json(workflow.phases[0]?.id ?? null)};`)
  emit("let status = 'running';")
  emit('try { await runtime.start(); workflow: while (next) { switch (next) {')
  workflow.phases.forEach((step, index) => {
    emit(`case ${json(step.id)}: {`)
    emit(`const result = await ${names.get(step.id)}(scope);`)
    emit(`scope.state[${json(step.id)}] = result.output;`)
    if (step.output_as) emit(`scope.state[${json(step.output_as)}] = result.output;`)
    emit(`await runtime.checkpoint(${json(step.id)}, scope);`)
    emit('next = result.next;')
    emit('if (!next) { switch (result.status) {')
    for (const outcome of ['success', 'failure', 'skipped', 'stagnation', 'reject_exhausted']) {
      const transition = step.transitions?.[`on_${outcome === 'skipped' ? 'skip' : outcome}`] ?? step.transitions?.[outcome]
      emit(`case ${json(outcome)}:`)
      if (transition) emit(`next = ${json(transition)}; break;`)
      else if (['success', 'skipped'].includes(outcome)) emit(`next = ${json(workflow.phases[index + 1]?.id ?? null)}; break;`)
      else emit("status = 'failed'; break workflow;")
    }
    emit("default: throw new Error('Invalid stage status'); } } break; }")
  })
  const terminals = Array.isArray(workflow.terminals) ? workflow.terminals : Object.keys(workflow.terminals ?? {})
  for (const terminal of terminals) emit(`case ${json(terminal)}: status = ${json(terminal === 'failed' ? 'failed' : terminal === 'blocked' ? 'blocked' : 'complete')}; break workflow;`)
  emit("default: throw new Error('Invalid compiled transition'); } }")
  emit("if (status === 'running') status = 'complete';")
  emit('return {status, terminal: next ?? null, state: scope.state};')
  emit('} finally { await runtime.finish(); }')
  return { script: lines.join('\n'), workflow, stages, sourceMap }
}

export async function compileWorkflow(workflow: Workflow, containerBackend = false): Promise<string> {
  return (await generateWorkflow(workflow, containerBackend)).script
}
