import { createHash } from 'node:crypto'
import { mkdir, realpath, rm } from 'node:fs/promises'
import { join, resolve as absolute } from 'node:path'
import { DomainBridge } from './bridge.js'
import { DshRuntime, type DshHost } from './dsh-runtime.js'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { WorkflowEngine } from '@deepseek-ai/dsh-workflow'
import { NativeWorkflow } from './native-workflow.js'
import type { WorkflowTransport } from './workflow-transport.js'
import { buildWorkflowPackage, loadWorkflowPackage, verifyPackageBindings, type WorkflowPackage } from './workflow-package.js'
import { RunStore } from './store.js'
import { createShellPhase } from './shell.js'
import { ContainerExecutionBackend, LocalExecutionBackend } from './execution-backend.js'
import type { Data, RunSnapshot } from './types.js'

export interface ControllerConfig {
  repository: string
  runRoot: string
  python: string
  workflows: Record<string, string>
  workflowPackages?: Record<string, string>
  roleTools: Record<string, string[]>
  roleOptions?: Record<string, Data>
  defaultTimeoutSeconds: number
  maxPromptBytes: number
}

interface ActiveRun { engine: NativeWorkflow; done: Promise<RunSnapshot> }

export class SeamController {
  private readonly active = new Map<string, ActiveRun>()
  private readonly chats = new Map<string, { runtime: DshRuntime; release(): Promise<void> }>()
  private readonly pending = new Set<Promise<unknown>>()
  private readonly packages = new Map<string, Promise<WorkflowPackage>>()
  private closing = false
  private readonly lifecycle = new AbortController()
  constructor(private readonly host: DshHost, private readonly config: ControllerConfig, private readonly workflowEngine: WorkflowEngine, private readonly transport: WorkflowTransport) {
    if (![config.repository, config.runRoot, config.python].every(value => typeof value === 'string' && value.length > 0)) throw new Error('repository, runRoot and python are required')
    if (!Number.isSafeInteger(config.maxPromptBytes) || !(config.maxPromptBytes > 0) || !Number.isFinite(config.defaultTimeoutSeconds) || !(config.defaultTimeoutSeconds > 0)) throw new Error('Explicit finite prompt and phase budgets are required')
    if (!config.workflows || !config.roleTools || Object.values(config.roleTools).some(tools => !Array.isArray(tools) || tools.some(tool => typeof tool !== 'string'))) throw new Error('Explicit workflows and role tool policies are required')
  }
  private track<T>(operation: () => Promise<T>): Promise<T> {
    if (this.closing) return Promise.reject(new Error('SEAM plugin is disposing'))
    const task = operation()
    this.pending.add(task)
    void task.finally(() => this.pending.delete(task)).catch(() => {})
    return task
  }
  private async lease(workspace: string): Promise<() => Promise<void>> {
    const root = join(this.config.runRoot, 'workspace-locks')
    await mkdir(root, { recursive: true })
    const lock = join(root, createHash('sha256').update(workspace).digest('hex'))
    await mkdir(lock)
    return () => rm(lock, { recursive: true })
  }
  private workflowPackage(name: string): Promise<WorkflowPackage> {
    const cached = this.packages.get(name)
    if (cached) return cached
    const explicit = this.config.workflowPackages && Object.hasOwn(this.config.workflowPackages, name) ? this.config.workflowPackages[name] : undefined
    const yaml = Object.hasOwn(this.config.workflows, name) ? this.config.workflows[name] : undefined
    if (!explicit && !yaml) return Promise.reject(new Error('Workflow is not configured in this profile'))
    const pending = explicit
      ? loadWorkflowPackage(absolute(this.config.repository, explicit), this.config.repository)
      : buildWorkflowPackage(absolute(this.config.repository, yaml!), join(this.config.runRoot, 'workflow-packages'), this.config.repository)
    this.packages.set(name, pending)
    void pending.catch(() => { this.packages.delete(name) })
    return pending
  }
  run(request: { runId: string; workflow: string; workspace: string; context: Data }, parent: Agent): Promise<{ runId: string; status: string }> {
    return this.track(async () => {
      if (!parent) throw new Error('A calling dsh Agent is required to own the workflow')
      if (this.active.has(request.runId) || this.chats.has(request.runId)) throw new Error('Run is already owned')
      const bundle = await this.workflowPackage(request.workflow)
      await verifyPackageBindings(bundle, this.config.repository)
      const workflow = structuredClone(bundle.workflow)
      const workspace = await realpath(request.workspace)
      const store = new RunStore(this.config.runRoot, request.runId)
      const backendConfig = workflow.execution_backend as Data | undefined
      const backendMode = backendConfig?.mode ?? 'local'
      if (backendMode === 'auto') throw new Error('execution_backend.mode=auto remains unavailable until deterministic backend selection is migrated')
      if (backendMode !== 'local' && backendMode !== 'container') throw new Error(`Unsupported execution_backend.mode: ${String(backendMode)}`)
      const containerBackend = backendMode === 'container' ? new ContainerExecutionBackend(backendConfig as never, workspace) : undefined
      const bridge = new DomainBridge({ repository: this.config.repository, python: this.config.python, outputDir: store.directory, maxPromptBytes: this.config.maxPromptBytes, agentSkills: Object.fromEntries(Object.entries(workflow.agents ?? {}).map(([role, agent]) => [role, (agent as Data).runtime_skills as Data ?? {}])) })
      for (const phase of [...workflow.phases, ...Object.values(workflow.sub_workflows ?? {}).flatMap(loop => loop.phases)]) {
        if ((phase.type ?? 'llm') === 'llm' && !Object.hasOwn(this.config.roleTools, phase.agent ?? '')) throw new Error(`Explicit tool policy missing: ${phase.agent}`)
      }
      const release = await this.lease(workspace)
      let engine: NativeWorkflow | undefined
      const runtime = new DshRuntime({ host: this.host, workspace, roleTools: this.config.roleTools, roleOptions: this.config.roleOptions, containerBackend, lifecycle: Object.fromEntries(Object.entries(workflow.agents ?? {}).map(([role, agent]) => [role, agent.lifecycle ?? 'persistent'])), render: execution => bridge.render(execution), onSessions: async () => { await engine?.checkpoint() }, onEvents: async (phase, events) => { await store.artifact(`${phase}-events`, events) } })
      try {
        this.lifecycle.signal.throwIfAborted()
        engine = new NativeWorkflow({ workflow, runtime, handlers: { builtin: bridge.builtin, shell: createShellPhase(containerBackend ?? new LocalExecutionBackend()) }, store, workspace, context: request.context, validate: (execution, result) => bridge.validate(execution, result), defaultTimeoutSeconds: this.config.defaultTimeoutSeconds, signal: this.lifecycle.signal, containerBackend: Boolean(containerBackend) }, this.workflowEngine, this.transport, parent, bundle)
        const done = engine.run().finally(async () => {
          try { await containerBackend?.close(new AbortController().signal) }
          finally { try { await release() } finally { this.active.delete(request.runId) } }
        })
        this.active.set(request.runId, { engine, done })
        void done.catch(() => {})
        return { runId: request.runId, status: 'starting' }
      } catch (error) {
        try { await runtime.dispose() }
        finally {
          try { await containerBackend?.close(new AbortController().signal) }
          finally { await release() }
        }
        throw error
      }
    })
  }
  async status(runId: string): Promise<RunSnapshot | { status: 'starting' }> {
    const active = this.active.get(runId)
    if (!active) return new RunStore(this.config.runRoot, runId).read()
    const snapshot = active.engine.status()
    return snapshot ? { ...snapshot, status: 'running' } : { status: 'starting' }
  }
  async cancel(runId: string): Promise<RunSnapshot> {
    const active = this.active.get(runId)
    if (!active) throw new Error('Run is not active in this plugin instance')
    active.engine.cancel()
    return active.done
  }
  restoreChat(runId: string): Promise<Record<string, string>> {
    return this.track(async () => {
      if (this.active.has(runId) || this.chats.has(runId)) throw new Error('Run is already owned')
      const snapshot = await new RunStore(this.config.runRoot, runId).read()
      if (snapshot.status === 'running') throw new Error('Run was not cleanly stopped; inspect locks and active processes before restoring chat')
      const release = await this.lease(await realpath(snapshot.workspace))
      const runtime = new DshRuntime({ host: this.host, workspace: snapshot.workspace, roleTools: this.config.roleTools, roleOptions: this.config.roleOptions, resumeSessions: snapshot.sessions, render: async () => { throw new Error('Chat restoration cannot execute workflow phases') } })
      try {
        const sessions = await runtime.restore(this.lifecycle.signal)
        this.chats.set(runId, { runtime, release })
        return sessions
      } catch (error) { await release(); throw error }
    })
  }
  async closeChat(runId: string): Promise<void> {
    const chat = this.chats.get(runId)
    if (!chat) throw new Error('Chat is not owned by this plugin instance')
    try { await chat.runtime.dispose() }
    finally { try { await chat.release() } finally { this.chats.delete(runId) } }
  }
  async dispose(): Promise<void> {
    this.closing = true
    this.lifecycle.abort(new Error('SEAM plugin disposed'))
    await Promise.allSettled([...this.pending])
    const runs = [...this.active.values()]
    for (const run of runs) run.engine.cancel()
    const results = await Promise.allSettled([...runs.map(run => run.done), ...[...this.chats.keys()].map(runId => this.closeChat(runId))])
    const errors = results.filter(result => result.status === 'rejected').map(result => (result as PromiseRejectedResult).reason)
    if (errors.length) throw new AggregateError(errors, 'SEAM plugin cleanup failed')
  }
}
