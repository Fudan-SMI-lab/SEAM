import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { DshRuntime, type AgentScope, type DshAgent, type DshEvent, type DshHost } from '../src/dsh-runtime.js'
import { ContainerExecutionBackend } from '../src/execution-backend.js'
import type { Execution } from '../src/types.js'

async function fixture(context: { after: (callback: () => Promise<void>) => void }) {
  const workspace = await mkdtemp(join(tmpdir(), 'seam-runtime-'))
  context.after(() => rm(workspace, { recursive: true, force: true }))
  const events: DshEvent[] = []
  const calls = { disposed: 0, created: 0, restricted: 0, registeredTools: [] as string[] }
  const agent: DshAgent = {
    id: 'chat', status: 'idle', session: { header: { cwd: workspace }, snapshotEvents: () => events },
    cancel: () => {}, whenIdle: async () => {},
    followup: message => {
      const turn = events.length
      events.push({ type: 'turn/start', data: { turn } }, { type: 'user/message', data: { id: message.id } }, { type: 'assistant/message', data: { turn, message: { content: [{ type: 'text', text: '{"ok":true}' }] } } }, { type: 'turn/end', data: { turn, reason: { kind: 'completed' } } })
    },
  }
  const handle = { agent, dispose: async () => { calls.disposed++ } }
  const host: DshHost = {
    agents: {
      create: async options => { calls.created++; options.setup({ tools: { restrict: () => { calls.restricted++; return () => {} }, register: (tool: { name: string }) => { calls.registeredTools.push(tool.name); return () => {} } } } as unknown as AgentScope); return handle },
      resume: async options => { options.setup({ tools: { restrict: () => { calls.restricted++; return () => {} }, register: (tool: { name: string }) => { calls.registeredTools.push(tool.name); return () => {} } } } as unknown as AgentScope); return handle },
    },
    message: text => ({ id: `message-${events.length}`, text }), flush: async () => {},
  }
  const execution: Execution = { runId: 'fixture', workspace, phase: { id: 'phase', agent: 'role' }, input: {}, signal: new AbortController().signal, scope: { context: {}, state: {}, globals: {}, loop_state: {}, loop_vars: {}, loop_history: [], step_outputs: {} } }
  return { workspace, host, agent, calls, execution }
}

test('container runtime shadows bash in each role scope while preserving the configured allowlist', async context => {
  const { workspace, host, calls, execution } = await fixture(context)
  const backend = new ContainerExecutionBackend({ mode: 'container', image: 'fixture:latest' }, workspace, async () => ({ exit_code: 0, stdout: '', stderr: '', duration: 0 }))
  const runtime = new DshRuntime({ host, workspace, roleTools: { role: ['read', 'bash'] }, containerBackend: backend, render: async () => '' })
  await runtime.execute(execution)
  assert.deepEqual(calls.registeredTools, ['bash'])
  assert.equal(calls.restricted, 1)
  await runtime.dispose()
})

test('workspace mismatch rejects restored chat and disposes its handle', async context => {
  const { workspace, host, agent, calls } = await fixture(context)
  agent.session.header.cwd = tmpdir()
  const runtime = new DshRuntime({ host, workspace, roleTools: { role: [] }, resumeSessions: { role: 'chat' }, render: async () => '' })
  await assert.rejects(runtime.restore(new AbortController().signal), /workspace does not match/)
  assert.equal(calls.disposed, 1)
  assert.equal(calls.restricted, 1)
})

test('checkpoint failure removes newly created identity and releases the role guard', async context => {
  const { workspace, host, calls, execution } = await fixture(context)
  let fail = true
  const runtime = new DshRuntime({ host, workspace, roleTools: { role: [] }, render: async () => '', onSessions: async () => { if (fail) throw new Error('checkpoint unavailable') } })
  await assert.rejects(runtime.execute(execution), /checkpoint unavailable/)
  assert.deepEqual(runtime.sessions(), {})
  assert.equal(calls.disposed, 1)
  fail = false
  assert.equal((await runtime.execute(execution)).status, 'success')
  assert.equal(calls.created, 2)
  await runtime.dispose()
})

test('persistence errors cannot pass a phase and do not prevent handle disposal', async context => {
  const { workspace, host, calls, execution } = await fixture(context)
  host.flush = async () => { throw new Error('persistence unavailable') }
  const runtime = new DshRuntime({ host, workspace, roleTools: { role: [] }, render: async () => '' })
  await assert.rejects(runtime.execute(execution), /persistence unavailable/)
  await assert.rejects(runtime.dispose(), /cleanup failed/)
  assert.equal(calls.disposed, 1)
})
