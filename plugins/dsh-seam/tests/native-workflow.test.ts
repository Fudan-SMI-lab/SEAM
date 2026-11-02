import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { Context } from '@deepseek-ai/cordis'
import SessionStore, { SessionId } from '@deepseek-ai/dsh-session'
import SessionProjectionRegistry from '@deepseek-ai/dsh-session-projection'
import AgentRegistry from '@deepseek-ai/dsh-agent'
import AgentLoop from '@deepseek-ai/dsh-agent-loop'
import LlmRuntime from '@deepseek-ai/dsh-llm'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import SubagentRuntime from '@deepseek-ai/dsh-subagent'
import WorkerThreadWorkflowEngine from '@deepseek-ai/dsh-workflow-worker-thread'
import { parse } from 'yaml'
import { WorkflowEngine, type EngineOptions } from '../src/engine.js'
import { NativeWorkflow } from '../src/native-workflow.js'
import { WorkflowTransport } from '../src/workflow-transport.js'
import { compileWorkflow } from '../src/workflow-compiler.js'
import { checkOperatorWorkflow } from '../src/preflight.js'
import { RunStore } from '../src/store.js'
import type { Data, Execution, Handler, Result, Runtime, Workflow } from '../src/types.js'

const success = (output: Data = {}): Result => ({ status: 'success', output })
const normalize = (value: unknown) => JSON.parse(JSON.stringify(value))

async function host(context: { after(callback: () => Promise<void>): void }) {
  const root = await mkdtemp(join(tmpdir(), 'seam-native-'))
  context.after(() => rm(root, { recursive: true, force: true }))
  const ctx = new Context()
  await ctx.plugin(LlmRuntime)
  await ctx.plugin(SessionStore)
  await ctx.plugin(SessionProjectionRegistry)
  await ctx.plugin(SystemPrompt)
  await ctx.plugin(ToolRuntime)
  await ctx.plugin(AgentRegistry)
  await ctx.plugin(AgentLoop, { agents: [] })
  await ctx.plugin(SubagentRuntime)
  await ctx.plugin(WorkerThreadWorkflowEngine, { disposeGraceMs: 50 })
  const transport = new WorkflowTransport()
  ctx.subagents.registerProvider(transport)
  const parent = await ctx.agents.create({ sessionId: SessionId('native-parent'), meta: { cwd: root } })
  context.after(() => ctx.fiber.dispose())
  return { root, ctx, transport, parent: parent.agent }
}

async function compare(context: { after(callback: () => Promise<void>): void }, workflow: Workflow, execute: Handler, globals: Data = {}) {
  const { root, ctx, transport, parent } = await host(context)
  const make = (runId: string) => {
    const calls: string[] = []
    let disposed = 0
    const handler: Handler = async execution => { calls.push(execution.phase.id); return execute(execution) }
    const runtime: Runtime = { execute: handler, sessions: () => ({}), dispose: async () => { disposed++ } }
    const options: EngineOptions = { workflow, runtime, handlers: { builtin: handler }, store: new RunStore(join(root, 'runs'), runId), workspace: root, context: globals, defaultTimeoutSeconds: 2 }
    return { options, calls, disposed: () => disposed }
  }
  const legacy = make('legacy')
  const native = make('native')
  const events: string[] = []
  ctx.on('workflow/phase', (_info, title) => { events.push(title) })
  const before = await new WorkflowEngine(legacy.options).run()
  const after = await new NativeWorkflow(native.options, ctx.workflowEngine, transport, parent).run()
  assert.equal(after.status, before.status, JSON.stringify(after))
  assert.equal(after.terminal, before.terminal)
  assert.deepEqual(normalize(after.state), normalize(before.state))
  assert.deepEqual(native.calls, legacy.calls)
  assert.equal(native.disposed(), 1)
  assert.ok(events.length)
  const artifacts = await readdir(native.options.store.directory)
  assert.ok(artifacts.some(name => name.endsWith('-dsh-script.json')))
  const resultName = artifacts.find(name => name.endsWith('-dsh-result.json'))!
  const result = JSON.parse(await readFile(join(native.options.store.directory, resultName), 'utf8'))
  assert.equal(result.stopReason, 'completed')
  assert.ok(result.agentsStarted > native.calls.length)
  return { before, after, native, root }
}

test('real KernelBench YAML executes two repair rounds inside dsh with legacy-equivalent state', async context => {
  const workflow = parse(await readFile(new URL('../../../src/workflows/npu_kernelbench_custom_op.yaml', import.meta.url), 'utf8')) as Workflow
  await compare(context, workflow, async execution => {
    switch (execution.phase.operation) {
      case 'operator_task_validate': return success({ operator_task_dir: '/task', operator_name: 'fixture', level: 'level4', solution_type: 'ascendc', definition_path: '/task/definition.json', workload_path: '/task/workload.jsonl' })
      case 'operator_runtime_detect': return success({ runtime_status: 'ready' })
      case 'operator_runtime_configure': return success({ environment_file: '/runtime.env', configured: true })
      case 'operator_artifact_initialize': return success({ initialized: true, errors: [] })
      case 'operator_artifact_pack': return success({ packed: true })
      case 'operator_artifact_validate': return success({ rebuild_required: execution.scope.loop_state.iteration === 1, candidate_evaluable: true, solution_ready: true })
      case 'operator_kernelbench_eval': return success({ operation: 'operator_kernelbench_eval', operator_passed: execution.scope.loop_state.iteration === 2, evaluation_status: 'completed', failure_category: execution.scope.loop_state.iteration === 1 ? 'compile_failed' : '' })
      case 'operator_evaluation_classify': return success({ repair_role: 'operator_fixer' })
      default: return success({ repair_role: 'operator_fixer' })
    }
  }, { OPERATOR_TASK_DIR: '/task', KERNELBENCH_ROOT: '/kernelbench' })
})

test('dsh stops on structured stagnation and preserves the same loop history', async context => {
  const workflow: Workflow = { name: 'stagnation', version: 1, terminals: { complete: '', failed: '' }, phases: [{ id: 'repair', type: 'loop', sub_workflow: 'repair', transitions: { on_success: 'complete', on_stagnation: 'failed' } }], sub_workflows: { repair: { max_iterations: 8, stagnation_threshold: 2, phases: [{ id: 'evaluate_operator', type: 'builtin', operation: 'operator_kernelbench_eval' }] } } }
  await compare(context, workflow, async () => success({ operator_passed: false, operation: 'operator_kernelbench_eval', error: 'unchanged evidence' }))
})

test('dsh dispatch, conditions, failure terminal and aliases match the old scheduler', async context => {
  const workflow: Workflow = { name: 'dispatch', version: 1, terminals: { blocked: '' }, phases: [{ id: 'choose', type: 'dispatch', route_field: '${context.ROLE}', routes: { fixer: 'fix' } }, { id: 'skip', type: 'builtin', operation: 'operator_trace_summary' }, { id: 'fix', type: 'builtin', operation: 'operator_trace_summary', output_as: 'fixed', transitions: { on_failure: 'blocked' } }] }
  const result = await compare(context, workflow, async () => ({ status: 'failure', output: { error: 'fixture' } }), { ROLE: 'fixer' })
  assert.equal(result.after.status, 'blocked')
  assert.deepEqual(result.native.calls, ['fix'])
})

test('fixed compiler rejects unsupported policies and duplicate RPC phase identities', async () => {
  const workflow: Workflow = { name: 'invalid', version: 1, phases: [{ id: 'same', type: 'builtin', operation: 'operator_trace_summary' }] }
  await assert.rejects(compileWorkflow({ ...workflow, invented: true }), /unknown field/)
  await assert.rejects(compileWorkflow({ ...workflow, sub_workflows: { duplicate: { max_iterations: 1, phases: workflow.phases } } }), /globally unique/)
  assert.throws(() => checkOperatorWorkflow({ ...workflow, sub_workflows: { duplicate: { max_iterations: 1, phases: workflow.phases } } }), /globally unique/)
})

test('native worker maps complete state and preserves scalar input types without host globals', async context => {
  const workflow: Workflow = { name: 'mapping', version: 1, phases: [
    { id: 'seed', type: 'builtin', operation: 'operator_trace_summary' },
    { id: 'consume', type: 'builtin', operation: 'operator_trace_summary', input_mapping: { previous: '${state.*}', enabled: '${seed.enabled}', count: '${seed.count}' } },
  ] }
  const result = await compare(context, workflow, async execution => {
    if (execution.phase.id === 'seed') return success({ enabled: false, count: 0, nested: { values: [1, 2] } })
    assert.equal(execution.input.enabled, false)
    assert.equal(execution.input.count, 0)
    assert.deepEqual(((execution.input.previous as Data).seed as Data).nested, { values: [1, 2] })
    return success({ mapped: true })
  })
  assert.equal(result.after.status, 'complete', JSON.stringify(result.after))
})

test('native lifecycle hooks persist startup state and write final summary without changing routing', async context => {
  const { root, ctx, transport, parent } = await host(context)
  const store = new RunStore(root, 'hooks')
  const workflow: Workflow = { name: 'hooks', version: 1, hooks: { workflow_start: [{ operation: 'noop', save_as: 'startup' }], workflow_end: [{ operation: 'write_summary', critical: true }] }, phases: [{ id: 'done', type: 'builtin', operation: 'operator_trace_summary' }] }
  const runtime: Runtime = { execute: async () => success(), sessions: () => ({}), dispose: async () => {} }
  const runner = new NativeWorkflow({ workflow, runtime, handlers: { builtin: async execution => {
    assert.ok((execution.scope.state.hooks as Data).workflow_start)
    return success({ checked: true })
  } }, store, workspace: root, context: {}, defaultTimeoutSeconds: 2 }, ctx.workflowEngine, transport, parent)
  const result = await runner.run()
  assert.equal(result.status, 'complete', JSON.stringify(result))
  assert.equal(result.currentPhase, 'done')
  assert.equal(result.executionEngine, 'dsh-workflow')
  assert.ok(result.dshWorkflowId)
  assert.ok((result.state.hooks as Data).workflow_start)
  assert.ok((result.state.hooks as Data).workflow_end)
  const summary = JSON.parse(await readFile(join(store.directory, 'summary.json'), 'utf8'))
  assert.deepEqual(summary.state.done, result.state.done)
  assert.equal(summary.state.done.checked, true)
})

test('dsh reaches max iterations without inventing a successful terminal', async context => {
  const workflow: Workflow = { name: 'limit', version: 1, terminals: { complete: '', failed: '' }, phases: [{ id: 'repair', type: 'loop', input_mapping: { OPERATOR_TASK_DIR: '/task' }, sub_workflow: 'repair', transitions: { on_success: 'complete', on_failure: 'failed' } }], sub_workflows: { repair: { max_iterations: 2, stagnation_threshold: 9, phases: [{ id: 'evaluate_operator', type: 'builtin', operation: 'operator_kernelbench_eval' }] } } }
  const result = await compare(context, workflow, async execution => success({ operator_passed: false, error: `round ${execution.scope.loop_state.iteration}` }))
  assert.equal(result.after.status, 'failed')
  assert.equal((result.after.state.repair as Data).loop_status, 'max_iterations')
  assert.equal(result.native.calls.length, 2)
})

test('native loop routes environment repair without invoking the code fixer', async context => {
  const workflow: Workflow = {
    name: 'environment', version: 1,
    agents: { operator_fixer: {}, operator_environment_fixer: {} },
    phases: [{ id: 'repair', type: 'loop', sub_workflow: 'repair' }],
    sub_workflows: { repair: {
      max_iterations: 1,
      stop_conditions: [{ condition: '$.operator_passed == true', status: 'success' }],
      phases: [
        { id: 'classify', type: 'builtin', operation: 'operator_evaluation_classify' },
        { id: 'choose', type: 'dispatch', route_field: '${classify.repair_role}', routes: { operator_environment_fixer: 'environment_fix', operator_fixer: 'code_fix' } },
        { id: 'code_fix', type: 'llm', agent: 'operator_fixer' },
        { id: 'environment_fix', type: 'llm', agent: 'operator_environment_fixer' },
        { id: 'evaluate_operator', type: 'builtin', operation: 'operator_kernelbench_eval' },
      ],
    } },
  }
  const result = await compare(context, workflow, async execution => success(execution.phase.id === 'classify' ? { repair_role: 'operator_environment_fixer' } : execution.phase.id === 'evaluate_operator' ? { operator_passed: true } : {}))
  assert.equal(result.after.status, 'complete')
  assert.deepEqual(result.native.calls, ['classify', 'environment_fix', 'evaluate_operator'])
})

test('critical native end hook cannot leave an otherwise complete run successful', async context => {
  const { root, ctx, transport, parent } = await host(context)
  const runtime: Runtime = { execute: async () => success(), sessions: () => ({}), dispose: async () => {} }
  const workflow: Workflow = { name: 'critical', version: 1, hooks: { workflow_end: [{ operation: 'snapshot_project', params: { project_dir: join(root, 'missing') }, critical: true }] }, phases: [{ id: 'done', type: 'builtin', operation: 'operator_trace_summary' }] }
  const runner = new NativeWorkflow({ workflow, runtime, handlers: { builtin: async () => success() }, store: new RunStore(root, 'critical'), workspace: root, context: {}, defaultTimeoutSeconds: 2 }, ctx.workflowEngine, transport, parent)
  const result = await runner.run()
  assert.equal(result.status, 'failed')
  assert.match(result.error ?? '', /Critical workflow_end hook failed/)
})

test('native phase timeout cancels domain work and returns only after cleanup', async context => {
  const { root, ctx, transport, parent } = await host(context)
  let cleaned = false
  const workflow: Workflow = { name: 'deadline', version: 1, phases: [{ id: 'wait', type: 'builtin', operation: 'operator_trace_summary', timeout: 0.02 }] }
  const runtime: Runtime = { execute: async () => success(), sessions: () => ({}), dispose: async () => {} }
  const execute: Handler = async execution => {
    await new Promise<void>((_resolve, reject) => execution.signal.addEventListener('abort', () => { setTimeout(() => { cleaned = true; reject(execution.signal.reason) }, 30) }, { once: true }))
    return success()
  }
  const runner = new NativeWorkflow({ workflow, runtime, handlers: { builtin: execute }, store: new RunStore(root, 'timeout'), workspace: root, context: {}, defaultTimeoutSeconds: 2 }, ctx.workflowEngine, transport, parent)
  const result = await runner.run()
  assert.equal(result.status, 'failed', JSON.stringify(result))
  assert.equal((result.state.wait as Data).failure_kind, 'phase_timeout')
  assert.equal(cleaned, true)
})

test('workflow cancellation drains operations past the dsh worker grace before releasing its store', async context => {
  const { root, ctx, transport, parent } = await host(context)
  let entered!: () => void
  const ready = new Promise<void>(resolve => { entered = resolve })
  let cleaned = false
  const execute: Handler = async execution => {
    entered()
    await new Promise<void>((_resolve, reject) => execution.signal.addEventListener('abort', () => { setTimeout(() => { cleaned = true; reject(execution.signal.reason) }, 150) }, { once: true }))
    return success()
  }
  const runtime: Runtime = { execute, sessions: () => ({}), dispose: async () => {} }
  const store = new RunStore(root, 'cancel')
  const workflow: Workflow = { name: 'cancel', version: 1, phases: [{ id: 'wait', type: 'builtin', operation: 'operator_trace_summary' }] }
  const runner = new NativeWorkflow({ workflow, runtime, handlers: { builtin: execute }, store, workspace: root, context: {}, defaultTimeoutSeconds: 2 }, ctx.workflowEngine, transport, parent)
  const done = runner.run()
  await ready
  runner.cancel()
  const result = await done
  assert.equal(result.status, 'cancelled', JSON.stringify(result))
  assert.equal(cleaned, true)
  assert.ok(!(await readdir(store.directory)).includes('lock'))
})

test('operation capabilities reject other parents and expired routes', async context => {
  const { ctx, transport, parent } = await host(context)
  const binding = transport.bind(parent, async () => ({}))
  const request = { prompt: [{ type: 'text' as const, text: JSON.stringify({ version: 1, token: binding.token, action: 'checkpoint' }) }], parent, signal: new AbortController().signal, descriptor: { version: 1 as const, mode: 'one-shot' as const, provider: transport.name } }
  await assert.rejects(transport.start({ ...request, parent: {} as typeof parent }), /capability/)
  await binding.release()
  await assert.rejects(transport.start(request), /capability/)
  assert.equal(ctx.subagents.getProvider(transport.name), transport)
})
