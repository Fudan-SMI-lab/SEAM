import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { parse } from 'yaml'
import { WorkflowEngine, checkWorkflow, type EngineOptions } from '../src/engine.js'
import { RunStore } from '../src/store.js'
import { shellPhase } from '../src/shell.js'
import { checkOperatorWorkflow } from '../src/preflight.js'
import type { Handler, Result, Runtime, Workflow } from '../src/types.js'

async function fixture(context: { after(fn: () => Promise<void>): void }, workflow: Workflow, execute: Handler, extra: Partial<EngineOptions> = {}) {
  const workspace = await mkdtemp(join(tmpdir(), 'seam-engine-'))
  context.after(() => rm(workspace, { recursive: true, force: true }))
  let disposed = 0
  const runtime: Runtime = { execute, sessions: () => ({ role: 'chat' }), dispose: async () => { disposed++ } }
  const store = new RunStore(join(workspace, 'runs'), 'fixture')
  const engine = new WorkflowEngine({ workflow, runtime, store, workspace, context: {}, handlers: { builtin: execute }, defaultTimeoutSeconds: 2, ...extra })
  return { engine, store, workspace, disposed: () => disposed }
}

const success = (output = {}): Result => ({ status: 'success', output })

test('real operator YAML passes preflight; unmigrated policies fail before execution', async () => {
  const workflow = parse(await readFile(new URL('../../../src/workflows/npu_kernelbench_custom_op.yaml', import.meta.url), 'utf8')) as Workflow
  assert.doesNotThrow(() => checkWorkflow(workflow, { builtin: async () => success() }))
  const project = parse(await readFile(new URL('../../../src/workflows/npu_ascend_custom_op.yaml', import.meta.url), 'utf8')) as Workflow
  assert.throws(() => checkWorkflow(project, {}), /Not migrated/)
  assert.throws(() => checkWorkflow({ ...workflow, invented_policy: {} }, {}), /unknown field/)
  assert.throws(() => checkWorkflow({ ...workflow, sub_workflows: { nested: { max_iterations: 1, phases: [], blocks: { ignored: { phases: [] } } } } }, { builtin: async () => success() }), /Unknown sub-workflow|blocks/)
})

test('real operator workflow carries validated task defaults through runtime, loop and report', async context => {
  const workflow = parse(await readFile(new URL('../../../src/workflows/npu_kernelbench_custom_op.yaml', import.meta.url), 'utf8')) as Workflow
  const workspace = await mkdtemp(join(tmpdir(), 'seam-operator-workflow-'))
  context.after(() => rm(workspace, { recursive: true, force: true }))
  const observed: Record<string, unknown>[] = []
  const runBuiltin: Handler = async execution => {
    observed.push({ phase: execution.phase.id, input: execution.input, loopVars: execution.scope.loop_vars })
    const operation = execution.phase.operation
    if (operation === 'operator_task_validate') return success({ operator_task_dir: '/tasks/example', operator_name: 'example', level: 'level3', solution_type: 'ascendc', definition_path: '/tasks/example/definition.py', workload_path: '/tasks/example/workloads.json', validation_passed: true })
    if (operation === 'operator_runtime_detect') return success({ runtime_status: 'ready' })
    if (operation === 'operator_runtime_configure') return success({ configured: true, environment_file: '/tmp/runtime.env' })
    if (operation === 'operator_artifact_initialize') return success({ initialized: true, errors: [] })
    if (operation === 'operator_artifact_pack') return success({ packed: true })
    if (operation === 'operator_artifact_validate') return success({ solution_ready: true, candidate_evaluable: true, rebuild_required: false })
    if (operation === 'operator_kernelbench_eval') return success({ operation, operator_passed: true, evaluation_status: 'completed', workload_pass_rate: 1, operator_pass_rate: 1 })
    if (operation === 'operator_evaluation_classify') return success({ repair_role: 'operator_fixer' })
    throw new Error(`Unexpected operator builtin: ${String(operation)}`)
  }
  const runtime: Runtime = {
    execute: async execution => {
      observed.push({ phase: execution.phase.id, input: execution.input, loopVars: execution.scope.loop_vars })
      return success({})
    },
    sessions: () => ({}),
    dispose: async () => {},
  }
  const store = new RunStore(join(workspace, 'runs'), 'operator-workflow')
  const engine = new WorkflowEngine({ workflow, runtime, handlers: { builtin: runBuiltin }, store, workspace, context: { OPERATOR_TASK_DIR: '/tasks/example', KERNELBENCH_ROOT: '/kernelbench' }, defaultTimeoutSeconds: 2 })
  const result = await engine.run()
  assert.equal(result.status, 'complete')
  const loopRun = observed.find(entry => entry.phase === 'evaluate_operator')
  const loopVars = loopRun?.loopVars as Record<string, unknown>
  assert.equal(loopVars.OPERATOR_LEVEL, 'level3')
  assert.equal(loopVars.SOLUTION_TYPE, 'ascendc')
  assert.equal(loopVars.OPERATOR_RUNTIME_CONFIGURATION && typeof loopVars.OPERATOR_RUNTIME_CONFIGURATION, 'object')
  assert.ok(observed.some(entry => entry.phase === 'phase_4_operator_report'))
})

test('explicit local backend matches host workspace execution and container mode remains gated', async () => {
  const local: Workflow = { name: 'local-backend', version: 1, execution_backend: { mode: 'local' }, phases: [{ id: 'x', type: 'builtin' }] }
  assert.doesNotThrow(() => checkWorkflow(local, { builtin: async () => success() }))
  assert.throws(() => checkWorkflow({ ...local, execution_backend: { mode: 'container' } }, { builtin: async () => success() }), /execution_backend.mode=container/)
  const container: Workflow = { name: 'container-backend', version: 1, execution_backend: { mode: 'container' }, agents: { role: {} }, phases: [{ id: 'build', agent: 'role' }, { id: 'compile', type: 'shell', command: ['make'] }] }
  assert.doesNotThrow(() => checkWorkflow(container, { shell: shellPhase }, { containerBackend: true }))
  assert.throws(() => checkWorkflow({ ...container, phases: [...container.phases, { id: 'legacy', type: 'builtin' }] }, { shell: shellPhase }, { containerBackend: true }), /No handler for phase type builtin/)
  assert.throws(() => checkOperatorWorkflow({ ...container, phases: [{ id: 'domain', type: 'builtin', operation: 'operator_trace_summary' }] }, { containerBackend: true }), /builtin phases remain blocked/)
})

test('loop promotes evaluation fields, records all phase types and takes success terminal', async context => {
  const workflow: Workflow = { name: 'loop', version: 1, globals: { max_repair_iterations: 0 }, terminals: { complete: '' }, phases: [{ id: 'repair', type: 'loop', sub_workflow: 'repair', transitions: { on_success: 'complete' } }], sub_workflows: { repair: { max_iterations: 2, stagnation_threshold: 4, stop_conditions: [{ condition: '$.operator_passed == true', status: 'success' }], phases: [{ id: 'evaluate_operator', type: 'builtin' }] } } }
  const { engine, store, disposed } = await fixture(context, workflow, async execution => success({ operation: 'operator_kernelbench_eval', operator_passed: execution.scope.loop_state.iteration === 2, evaluation_status: 'completed', unexpected_scalar: 'not promoted' }))
  const snapshot = await engine.run()
  assert.equal(snapshot.status, 'complete')
  assert.equal(snapshot.terminal, 'complete')
  assert.equal((snapshot.state.repair as Record<string, unknown>).iteration, 2)
  assert.equal((snapshot.state.repair as Record<string, unknown>).unexpected_scalar, undefined)
  assert.equal(disposed(), 1)
  const files = await readdir(store.directory)
  assert.equal(files.filter(name => name.endsWith('-evaluate_operator.json')).length, 2)
  assert.equal(files.filter(name => name.endsWith('-repair.json')).length, 1)
  assert.equal(files.filter(name => name.endsWith('-repair-iteration.json')).length, 2)
  assert.deepEqual((await store.read()).state, snapshot.state)
})

test('stagnation uses captured evidence, not an analyzer invented signature', async context => {
  const workflow: Workflow = { name: 'stagnation', version: 1, agents: { role: {} }, phases: [{ id: 'loop', type: 'loop', sub_workflow: 'repair' }], sub_workflows: { repair: { max_iterations: 9, stagnation_threshold: 2, phases: [{ id: 'analyze', agent: 'role', output_as: 'error_analysis' }] } } }
  const { engine } = await fixture(context, workflow, async execution => success({ error_signature: `invented-${execution.scope.loop_state.iteration}` }))
  const snapshot = await engine.run()
  const loop = snapshot.state.loop as Record<string, unknown>
  assert.equal(snapshot.status, 'failed')
  assert.equal(loop.framework_execution_status, 'stagnation')
  assert.equal(loop.iteration, 2)
})

test('stop expression errors cannot accidentally declare success', async context => {
  const workflow: Workflow = { name: 'stop', version: 1, phases: [{ id: 'loop', type: 'loop', sub_workflow: 'repair' }], sub_workflows: { repair: { max_iterations: 1, stop_conditions: [{ condition: '(', status: 'success' }], phases: [{ id: 'probe', type: 'builtin' }] } } }
  const { engine, store } = await fixture(context, workflow, async () => success())
  const snapshot = await engine.run()
  assert.equal(snapshot.status, 'failed')
  assert.ok((await readdir(store.directory)).some(name => name.includes('stop-condition')))
})

test('workflow lifecycle hooks write snapshots and summary, including final state', async context => {
  const workflow: Workflow = {
    name: 'hooks', version: 1,
    hooks: {
      workflow_start: [{ operation: 'snapshot_project', save_as: 'before' }],
      workflow_end: [{ operation: 'snapshot_project', save_as: 'after' }, { operation: 'write_summary' }],
    },
    phases: [{ id: 'finish', type: 'builtin' }],
  }
  const { engine, store, workspace } = await fixture(context, workflow, async () => success({ done: true }))
  await writeFile(join(workspace, 'sample.py'), 'value = 1\n')
  const snapshot = await engine.run()
  assert.equal(snapshot.status, 'complete')
  assert.equal((snapshot.state.hooks as Record<string, Record<string, unknown>>).workflow_end?.after && true, true)
  const files = await readdir(store.directory)
  assert.ok(files.includes('before_snapshot.json'))
  assert.ok(files.includes('after_snapshot.json'))
  const summary = JSON.parse(await readFile(join(store.directory, 'summary.json'), 'utf8')) as { state: Record<string, unknown> }
  assert.equal((summary.state.finish as Record<string, unknown>).done, true)
})

test('shell phase captures configured fields and routes on its real exit status', async context => {
  const workflow: Workflow = { name: 'shell', version: 1, phases: [{ id: 'command', type: 'shell', command: 'printf "$VALUE"; printf err >&2', env: { VALUE: 'ok' }, capture: { stdout: 'captured_stdout', stderr: 'captured_stderr', exit_code: 'script_exit_code' } }] }
  const { engine } = await fixture(context, workflow, async () => success(), { handlers: { builtin: async () => success(), shell: shellPhase } })
  const snapshot = await engine.run()
  assert.equal(snapshot.status, 'complete')
  const output = snapshot.state.command as Record<string, unknown>
  assert.equal(output.captured_stdout, 'ok')
  assert.equal(output.captured_stderr, 'err')
  assert.equal(output.script_exit_code, 0)
})

test('nonzero shell exit is a framework failure, not a successful agent claim', async context => {
  const workflow: Workflow = { name: 'shell-failure', version: 1, phases: [{ id: 'command', type: 'shell', command: 'exit 9' }] }
  const { engine } = await fixture(context, workflow, async () => success(), { handlers: { builtin: async () => success(), shell: shellPhase } })
  const snapshot = await engine.run()
  assert.equal(snapshot.status, 'failed')
  assert.equal((snapshot.state.command as Record<string, unknown>).exit_code, 9)
})

test('noncritical lifecycle hook errors are recorded without failing the workflow', async context => {
  const workflow: Workflow = { name: 'optional-hook', version: 1, hooks: { workflow_start: [{ operation: 'snapshot_project', critical: false, params: { project_dir: '/missing/seam-hook-project' } }] }, phases: [{ id: 'finish', type: 'builtin' }] }
  const { engine } = await fixture(context, workflow, async () => success())
  const snapshot = await engine.run()
  assert.equal(snapshot.status, 'complete')
  assert.equal(((snapshot.state.hooks as Record<string, Record<string, Record<string, unknown>>>).workflow_start?.snapshot_project?.success), false)
})

test('critical workflow-end hook failure fails the terminal result', async context => {
  const workflow: Workflow = { name: 'critical-hook', version: 1, hooks: { workflow_end: [{ operation: 'snapshot_project', critical: true, params: { project_dir: '/missing/seam-hook-project' } }] }, phases: [{ id: 'finish', type: 'builtin' }] }
  const { engine } = await fixture(context, workflow, async () => success())
  const snapshot = await engine.run()
  assert.equal(snapshot.status, 'failed')
  assert.match(snapshot.error ?? '', /Critical workflow_end hook failed/)
})

test('dispatch follows a typed route and never executes the unselected role', async context => {
  const calls: string[] = []
  const workflow: Workflow = { name: 'dispatch', version: 1, agents: { role: {} }, phases: [{ id: 'route', type: 'dispatch', route_field: '${context.route}', routes: { fix: 'fix' } }, { id: 'skip', agent: 'role' }, { id: 'fix', agent: 'role' }] }
  const { engine } = await fixture(context, workflow, async execution => { calls.push(execution.phase.id); return success() }, { context: { route: { role: 'fix' } } })
  assert.equal((await engine.run()).status, 'complete')
  assert.deepEqual(calls, ['fix'])
})

test('nested dispatch excludes other targets and falls back from empty routes to transitions', async context => {
  const calls: string[] = []
  const workflow: Workflow = {
    name: 'nested-dispatch', version: 1, agents: { role: {} },
    phases: [{ id: 'loop', type: 'loop', sub_workflow: 'repair' }],
    sub_workflows: { repair: {
      max_iterations: 1, stop_conditions: [{ condition: 'True', status: 'success' }],
      phases: [
        { id: 'route', type: 'dispatch', route_field: '${context.route}', params: { routes: {} }, transitions: { fix: 'fix', env: 'env' } },
        { id: 'env', agent: 'role' }, { id: 'fix', agent: 'role' }, { id: 'evaluate', type: 'builtin' },
      ],
    } },
  }
  const { engine } = await fixture(context, workflow, async execution => { calls.push(execution.phase.id); return success() }, { context: { route: 'fix' } })
  assert.equal((await engine.run()).status, 'complete')
  assert.deepEqual(calls, ['fix', 'evaluate'])
})

test('dispatch scalar conversion preserves Python null, boolean and numeric keys', async context => {
  for (const [value, route] of [[null, 'None'], [true, 'True'], [2, '2'], [{ value: null, role: 'wrong' }, 'None']] as const) {
    const calls: string[] = []
    const workflow: Workflow = { name: 'scalar-dispatch', version: 1, agents: { role: {} }, phases: [
      { id: 'route', type: 'dispatch', route_field: '${context.route}', routes: { [route]: 'fix' } },
      { id: 'fix', agent: 'role' },
    ] }
    const { engine } = await fixture(context, workflow, async execution => { calls.push(execution.phase.id); return success() }, { context: { route: value } })
    assert.equal((await engine.run()).status, 'complete')
    assert.deepEqual(calls, ['fix'])
  }
})

test('deadline includes validator; cleanup and checkpoint complete before return', async context => {
  const workflow: Workflow = { name: 'timeout', version: 1, phases: [{ id: 'validate', type: 'builtin', validator: 'fixture', timeout: 0.01 }] }
  const { engine, store, disposed } = await fixture(context, workflow, async () => success(), { validate: async (_execution, value) => { await new Promise(resolve => setTimeout(resolve, 25)); return value } })
  const snapshot = await engine.run()
  assert.equal(snapshot.status, 'failed')
  assert.equal((snapshot.state.validate as Record<string, unknown>).failure_kind, 'phase_timeout')
  assert.equal(disposed(), 1)
  assert.equal((await store.read()).status, 'failed')
})

test('JSON and validation correction attempts stay in the same role phase', async context => {
  const corrections: (string | undefined)[] = []
  const workflow: Workflow = { name: 'correction', version: 1, agents: { role: {} }, phases: [{ id: 'analysis', agent: 'role', validator: 'fixture' }] }
  let calls = 0
  const { engine } = await fixture(context, workflow, async execution => {
    corrections.push(execution.correction)
    calls++
    return calls === 1 ? { status: 'failure', output: { failure_kind: 'invalid_json_output' } } : success({ ok: calls >= 3 })
  }, { validate: async (_execution, value) => value.output.ok ? value : { status: 'failure', output: { ...value.output, validation_errors: ['ok must be true'] } } })
  assert.equal((await engine.run()).status, 'complete')
  assert.equal(calls, 3)
  assert.equal(corrections[0], undefined)
  assert.ok(corrections[1]?.includes('JSON object'))
  assert.ok(corrections[2]?.includes('ok must be true'))
})
