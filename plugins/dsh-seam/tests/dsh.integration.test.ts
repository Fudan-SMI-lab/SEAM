import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import AgentRegistry from '@deepseek-ai/dsh-agent'
import AgentLoop from '@deepseek-ai/dsh-agent-loop'
import SessionStore, { SessionId } from '@deepseek-ai/dsh-session'
import SessionProjectionRegistry from '@deepseek-ai/dsh-session-projection'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import LlmRuntime, { LlmAdapter, ToolCallId, type GenerateOptions, type StreamChunk } from '@deepseek-ai/dsh-llm'
import JsonlPersistence from '@deepseek-ai/dsh-session-persistence-jsonl'
import SubagentRuntime from '@deepseek-ai/dsh-subagent'
import WorkerThreadWorkflowEngine from '@deepseek-ai/dsh-workflow-worker-thread'
import { stringify } from 'yaml'
import * as SeamPlugin from '../src/plugin.js'
import type { SeamController, ControllerConfig } from '../src/controller.js'
import type { Data, RunSnapshot } from '../src/types.js'

const repository = resolve(fileURLToPath(new URL('../../../', import.meta.url)))

class ScriptedAdapter extends LlmAdapter {
  requests: GenerateOptions[] = []
  constructor(private readonly scripts: (StreamChunk[] | 'hang')[]) { super() }
  override async resolveModel(provider: string, model: string) { return { provider, id: model, name: model } }
  async *stream(options: GenerateOptions): AsyncIterable<StreamChunk> {
    this.requests.push(options)
    const entry = this.scripts.shift()
    if (entry === 'hang') {
      await new Promise<void>((_resolve, reject) => {
        const fail = () => reject(new Error('fixture cancelled'))
        if (options.signal?.aborted) fail()
        else options.signal?.addEventListener('abort', fail, { once: true })
      })
    } else {
      if (!entry) throw new Error('Fixture script exhausted')
      for (const chunk of entry) yield chunk
    }
  }
}

function response(value: string, kind: 'stop' | 'max-tokens' = 'stop'): StreamChunk[] {
  return [
    { type: 'block-start', index: 0, blockType: 'text' },
    { type: 'text-delta', index: 0, text: value },
    { type: 'block-end', index: 0, block: { type: 'text', text: value } },
    { type: 'finish', reason: { kind } },
  ]
}

async function setup(root: string, adapter: ScriptedAdapter, config: ControllerConfig, probe: () => void = () => {}) {
  const ctx = new Context()
  await ctx.plugin(LlmRuntime)
  await ctx.plugin(SessionStore)
  await ctx.plugin(SessionProjectionRegistry)
  await ctx.plugin(SystemPrompt)
  await ctx.plugin(ToolRuntime)
  ctx.tools.register({ name: 'fixture_probe', description: 'Fixture probe', parameters: { type: 'object' }, output: { schema: { type: 'object' }, render: () => [] }, execute: async () => { probe(); return {} } })
  await ctx.plugin(AgentRegistry)
  await ctx.plugin(JsonlPersistence, { root: join(root, 'chats'), compression: 'none' })
  await ctx.plugin(AgentLoop, { agents: [] })
  await ctx.plugin(SubagentRuntime)
  await ctx.plugin(WorkerThreadWorkflowEngine, {})
  ctx.llm.registerAdapter(['fixture'], adapter)
  const modules = new Map<string, unknown>([['seam-plugin', SeamPlugin]])
  ctx.baseUrl = pathToFileURL(root).href + '/'
  await ctx.plugin(Loader)
  ctx.loader.builtins.include = Include
  ctx.loader.internal = {
    version: 'v2',
    async import(specifier: string) {
      if (!modules.has(specifier)) throw new Error(`Unexpected Loader import: ${specifier}`)
      return modules.get(specifier)
    },
  } as unknown as NonNullable<typeof ctx.loader.internal>
  const profile = join(root, 'cordis.yml')
  await writeFile(profile, stringify([{ name: 'seam-plugin', config }]))
  await ctx.loader.create({ name: 'cordis:include', config: { path: pathToFileURL(profile).href } })
  await ctx.loader.await()
  const controller = ctx.get('seamWorkflow') as SeamController
  assert.ok(controller, 'Loader must actually activate the plugin')
  const parent = await ctx.agents.create({ sessionId: SessionId(`parent-${Date.now()}`), meta: { cwd: root } })
  return { ctx, controller, parent: parent.agent }
}

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'seam-real-dsh-'))
  const workflow = join(root, 'workflow.yaml')
  const phase = (id: string, agent: string) => ({ id, agent, prompt_template: 'phase_2_operator_analysis', input_mapping: { definition_path: 'definition.py', workload_path: 'workloads.json', runtime_report: {}, runtime_configuration: {} } })
  await writeFile(workflow, stringify({ name: 'fixture', version: 1, agents: { engineer: {}, fixer: {} }, phases: [phase('analysis', 'engineer'), phase('repair', 'fixer'), phase('report', 'engineer')] }))
  const config: ControllerConfig = { repository, runRoot: join(root, 'runs'), python: process.env.SEAM_TEST_PYTHON ?? 'python3', workflows: { fixture: workflow }, roleTools: { engineer: ['fixture_probe'], fixer: [] }, roleOptions: { engineer: { provider: 'fixture', model: 'fixture' }, fixer: { provider: 'fixture', model: 'fixture' } }, defaultTimeoutSeconds: 20, maxPromptBytes: 200_000 }
  return { root, workflow, config }
}

async function settled(controller: SeamController, runId: string): Promise<RunSnapshot> {
  for (let attempt = 0; attempt < 1000; attempt++) {
    try {
      const status = await controller.status(runId)
      if (status.status !== 'starting' && status.status !== 'running') return status
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
    await new Promise(resolve => setTimeout(resolve, 10))
  }
  throw new Error('Fixture did not settle')
}

test('real dsh JSON correction reuses the same role chat without rerunning the workflow', { timeout: 30000 }, async context => {
  const { root, workflow, config } = await fixture()
  context.after(() => rm(root, { recursive: true, force: true }))
  await writeFile(workflow, stringify({ name: 'fixture', version: 1, agents: { engineer: {} }, phases: [{ id: 'analysis', agent: 'engineer', prompt_template: 'phase_2_operator_analysis', input_mapping: { definition_path: '', workload_path: '', runtime_report: {}, runtime_configuration: {} } }] }))
  const adapter = new ScriptedAdapter([response('not a JSON object'), response('{"corrected":true}')])
  const { ctx, controller, parent } = await setup(root, adapter, config)
  context.after(() => ctx.fiber.dispose())
  await controller.run({ runId: 'correction', workflow: 'fixture', workspace: root, context: {} }, parent)
  const status = await settled(controller, 'correction')
  assert.equal(status.status, 'complete', JSON.stringify(status))
  assert.equal((status.state.analysis as Data).corrected, true)
  assert.equal(adapter.requests.length, 2)
  assert.equal(Object.keys(status.sessions).length, 1)
  await controller.restoreChat('correction')
  const agent = ctx.agents.get(SessionId(status.sessions.engineer!))!
  assert.equal(agent.session.snapshotEvents().filter(event => event.type === 'user/message').length, 2)
  await controller.closeChat('correction')
})

test('real Loader, AgentLoop and JSONL: tools, separate/reused role chats, cold restore and disposal', { timeout: 30000 }, async context => {
  const { root, config } = await fixture()
  context.after(() => rm(root, { recursive: true, force: true }))
  const call = { type: 'tool-call' as const, id: ToolCallId('probe'), name: 'fixture_probe', arguments: '{}' }
  const adapter = new ScriptedAdapter([
    [{ type: 'block-start', index: 0, blockType: 'tool-call' }, { type: 'block-end', index: 0, block: call }, { type: 'finish', reason: { kind: 'tool-calls' } }],
    response('{"ok":1}'), response('{"fixed":true}'), response('{"ok":2}'),
  ])
  let calls = 0
  let { ctx, controller, parent } = await setup(root, adapter, config, () => { calls++ })
  context.after(async () => { await ctx.fiber.dispose() })
  await controller.run({ runId: 'real', workflow: 'fixture', workspace: root, context: {} }, parent)
  const status = await settled(controller, 'real')
  assert.equal(status.status, 'complete', JSON.stringify(status))
  assert.equal(calls, 1)
  assert.equal((status.state.analysis as Data).ok, 1)
  assert.equal((status.state.report as Data).ok, 2)
  assert.notEqual(status.sessions.engineer, status.sessions.fixer)
  assert.equal(Object.keys(status.sessions).length, 2)
  assert.equal(adapter.requests.length, 4)
  assert.ok(adapter.requests[0]?.tools?.some(tool => tool.name === 'fixture_probe'))
  assert.ok(!adapter.requests[2]?.tools?.some(tool => tool.name === 'fixture_probe'))
  const saved = JSON.parse(await readFile(join(config.runRoot, 'real/state.json'), 'utf8'))
  assert.deepEqual(saved.sessions, status.sessions)
  assert.equal(ctx.agents.get(SessionId(status.sessions.engineer!)), undefined)
  await ctx.fiber.dispose()
  const second = await setup(root, new ScriptedAdapter([]), config)
  ctx = second.ctx
  controller = second.controller
  const sessions = await controller.restoreChat('real')
  assert.deepEqual(sessions, status.sessions)
  const restored = ctx.agents.get(SessionId(sessions.engineer!))
  assert.ok(restored)
  assert.equal(restored.status, 'idle')
  assert.equal(restored.session.snapshotEvents().filter(event => event.type === 'user/message').length, 2)
  await controller.closeChat('real')
  assert.equal(ctx.agents.get(SessionId(sessions.engineer!)), undefined)
  await ctx.fiber.dispose()
  assert.equal(ctx.get('seamWorkflow'), undefined)
})

test('real dsh cancellation drains LLM before returning and truncated turns never pass', { timeout: 30000 }, async context => {
  const { root, workflow, config } = await fixture()
  context.after(() => rm(root, { recursive: true, force: true }))
  const adapter = new ScriptedAdapter(['hang'])
  const { ctx, controller, parent } = await setup(root, adapter, config)
  context.after(() => ctx.fiber.dispose())
  await controller.run({ runId: 'cancel', workflow: 'fixture', workspace: root, context: {} }, parent)
  for (let attempt = 0; attempt < 300 && !adapter.requests.length; attempt++) await new Promise(resolve => setTimeout(resolve, 10))
  assert.ok(adapter.requests.length, JSON.stringify(await controller.status('cancel')))
  const cancelled = await controller.cancel('cancel')
  assert.equal(cancelled.status, 'cancelled')
  for (const id of Object.values(cancelled.sessions)) assert.equal(ctx.agents.get(SessionId(id)), undefined)
  await ctx.fiber.dispose()
  await writeFile(workflow, stringify({ name: 'fixture', version: 1, agents: { engineer: {} }, phases: [{ id: 'truncated', agent: 'engineer', prompt_template: 'phase_2_operator_analysis', input_mapping: { definition_path: '', workload_path: '', runtime_report: {}, runtime_configuration: {} } }] }))
  const next = await setup(root, new ScriptedAdapter([response('{"looks_valid":true}', 'max-tokens')]), config)
  context.after(() => next.ctx.fiber.dispose())
  await next.controller.run({ runId: 'truncated', workflow: 'fixture', workspace: root, context: {} }, next.parent)
  const failed = await settled(next.controller, 'truncated')
  assert.equal(failed.status, 'failed')
  assert.equal((failed.state.truncated as Data).failure_kind, 'runtime_turn_failed')
})

test('dsh workflow routes a real operator builtin to Python without invoking the model', async context => {
  const { root, workflow, config } = await fixture()
  context.after(() => rm(root, { recursive: true, force: true }))
  await writeFile(join(root, 'definition.json'), JSON.stringify({ name: 'smoke', reference: 'def run(value):\n    return value\n' }))
  await writeFile(join(root, 'workload.jsonl'), '{"shape":[16]}\n')
  await writeFile(workflow, stringify({ name: 'domain-smoke', version: 1, phases: [{ id: 'validate_task', type: 'builtin', operation: 'operator_task_validate' }] }))
  const adapter = new ScriptedAdapter([])
  const { ctx, controller, parent } = await setup(root, adapter, config)
  context.after(() => ctx.fiber.dispose())
  await controller.run({ runId: 'domain', workflow: 'fixture', workspace: root, context: { OPERATOR_TASK_DIR: root } }, parent)
  const result = await settled(controller, 'domain')
  assert.equal(result.status, 'complete', JSON.stringify(result))
  assert.equal((result.state.validate_task as Data).validation_passed, true)
  assert.equal(adapter.requests.length, 0)
  assert.equal(result.executionEngine, 'dsh-workflow')
  assert.ok(result.dshWorkflowId)
})
