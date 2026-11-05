import { createHash } from 'node:crypto'
import { realpath } from 'node:fs/promises'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { WorkflowEngine, WorkflowRun } from '@deepseek-ai/dsh-workflow'
import { generateWorkflow, type CompiledWorkflow } from './workflow-compiler.js'
import { executePhase } from './phase-executor.js'
import { executeWorkflowHooks } from './hooks.js'
import { WorkflowTransport, type WorkflowCommand } from './workflow-transport.js'
import type { EngineOptions } from './engine.js'
import type { Data, Phase, Result, RunSnapshot, Scope } from './types.js'

export class NativeWorkflow {
  private readonly cancellation = new AbortController()
  private snapshot?: RunSnapshot
  private native?: WorkflowRun
  private started = false
  private compiled?: CompiledWorkflow
  constructor(private readonly options: EngineOptions, private readonly engine: WorkflowEngine, private readonly transport: WorkflowTransport, private readonly parent: Agent, private readonly bundle?: CompiledWorkflow & { id?: string }) {}
  status(): RunSnapshot | undefined { return this.snapshot && structuredClone(this.snapshot) }
  cancel(): void { this.cancellation.abort(new Error('Workflow cancelled')); this.native?.cancel('SEAM workflow cancelled') }
  async checkpoint(): Promise<void> {
    if (!this.snapshot) return
    this.snapshot.sessions = this.options.runtime.sessions()
    await this.options.store.snapshot(this.snapshot)
  }
  private async hooks(point: 'workflow_start' | 'workflow_end'): Promise<void> {
    const hooks = this.options.workflow.hooks?.[point] ?? []
    if (!hooks.length) return
    const snapshot = this.snapshot!
    const directory = this.options.outputDirectory ?? this.options.store.directory
    const results = await executeWorkflowHooks(hooks, { ...snapshot.context, state: snapshot.state, phase_results: snapshot.state, status: snapshot.status, output_dir: directory, _hook_point: point }, directory, snapshot.workspace)
    snapshot.state.hooks = { ...((snapshot.state.hooks as Data | undefined) ?? {}), [point]: results }
    await this.options.store.artifact(`hooks-${point}`, results)
    await this.checkpoint()
  }
  private async operation(command: WorkflowCommand, signal: AbortSignal): Promise<Data> {
    signal.throwIfAborted()
    const snapshot = this.snapshot!
    if (command.action === 'artifact') {
      if (typeof command.name !== 'string') throw new Error('Artifact name is required')
      await this.options.store.artifact(command.name, command.value)
      return {}
    }
    if (command.action === 'checkpoint' || command.action === 'hooks') {
      if (!command.state || typeof command.state !== 'object' || Array.isArray(command.state)) throw new Error('Checkpoint state must be an object')
      snapshot.state = structuredClone(command.state as Data)
      if (command.action === 'hooks') {
        if (command.point !== 'workflow_start') throw new Error('Only workflow_start is callable from the script')
        await this.hooks('workflow_start')
      } else {
        if (typeof command.currentPhase !== 'string') throw new Error('Checkpoint phase is required')
        snapshot.currentPhase = command.currentPhase
        await this.checkpoint()
      }
      return { state: snapshot.state }
    }
    const phase = typeof command.phaseId === 'string' && this.compiled && Object.hasOwn(this.compiled.stages, command.phaseId) ? this.compiled.stages[command.phaseId] : undefined
    if (!phase || ['loop', 'dispatch'].includes(phase.type ?? 'llm')) throw new Error('Unknown leaf phase')
    if (!command.scope || typeof command.scope !== 'object' || Array.isArray(command.scope)) throw new Error('Phase scope is required')
    const scope = structuredClone(command.scope) as Scope
    const execution = { runId: snapshot.runId, workspace: snapshot.workspace, phase: phase as Phase, input: command.input as Data, scope, signal }
    const handler = !phase.type || phase.type === 'llm' ? this.options.runtime.execute.bind(this.options.runtime) : this.options.handlers[phase.type]
    if (!handler) throw new Error(`No leaf handler: ${phase.type}`)
    return await executePhase(execution, handler, this.options.validate, (name, value) => this.options.store.artifact(name, value), this.options.defaultTimeoutSeconds) as unknown as Data
  }
  async run(): Promise<RunSnapshot> {
    if (this.started) throw new Error('Workflow may only start once')
    this.started = true
    this.compiled = this.bundle ?? await generateWorkflow(this.options.workflow, this.options.containerBackend)
    if (JSON.stringify(this.compiled.workflow) !== JSON.stringify(this.options.workflow)) throw new Error('Workflow package does not match execution configuration')
    const script = this.compiled.script
    const signal = this.options.signal ? AbortSignal.any([this.cancellation.signal, this.options.signal]) : this.cancellation.signal
    await this.options.store.acquire()
    let binding: ReturnType<WorkflowTransport['bind']> | undefined
    let result: RunSnapshot | undefined
    try {
      this.snapshot = { format: 1, runId: this.options.store.runId, workflow: this.options.workflow.name, workflowHash: createHash('sha256').update(JSON.stringify(this.options.workflow)).digest('hex'), workspace: await realpath(this.options.workspace), status: 'running', state: {}, context: structuredClone(this.options.context), sessions: this.options.runtime.sessions(), executionEngine: 'dsh-workflow' }
      if (this.bundle?.id) this.snapshot.workflowPackageId = this.bundle.id
      await this.checkpoint()
      await this.options.store.artifact('dsh-script', { engine: 'dsh-workflow', script, sha256: createHash('sha256').update(script).digest('hex'), workflow: this.options.workflow })
      signal.throwIfAborted()
      binding = this.transport.bind(this.parent, (command, callerSignal) => this.operation(command, AbortSignal.any([signal, callerSignal])))
      this.native = this.engine.start({ script, meta: { name: this.options.workflow.name, description: 'Compiled SEAM workflow' }, args: { context: this.options.context, token: binding.token }, subagentProvider: this.transport.name, parent: this.parent, signal })
      this.snapshot.dshWorkflowId = this.native.id
      await this.checkpoint()
      await this.options.store.artifact('dsh-run', { id: this.native.id, provider: this.transport.name })
      const nativeResult = await this.native.result
      await this.options.store.artifact('dsh-result', nativeResult)
      if (nativeResult.stopReason !== 'completed') throw new Error(nativeResult.error ?? `dsh workflow ended: ${nativeResult.stopReason}`)
      signal.throwIfAborted()
      const value = nativeResult.value as Data
      if (!value || !['complete', 'failed', 'blocked'].includes(String(value.status)) || !value.state || typeof value.state !== 'object' || Array.isArray(value.state)) throw new Error('Invalid dsh workflow final result')
      this.snapshot.status = value.status as RunSnapshot['status']
      this.snapshot.state = value.state as Data
      if (typeof value.terminal === 'string') this.snapshot.terminal = value.terminal
    } catch (error) {
      if (!this.snapshot) throw error
      this.snapshot.status = signal.aborted ? 'cancelled' : 'failed'
      this.snapshot.error = error instanceof Error ? error.message : String(error)
    } finally {
      try {
        try { await this.native?.dispose() }
        catch (error) { this.fail(`dsh cleanup failed: ${String(error)}`) }
        await binding?.release()
        try { if (this.snapshot) await this.hooks('workflow_end') }
        catch (error) { this.fail(`Critical workflow_end hook failed: ${String(error)}`) }
        try { await this.options.runtime.dispose() }
        catch (error) { this.fail(`Runtime cleanup failed: ${String(error)}`) }
        await this.checkpoint()
        result = this.status()
      } finally { await this.options.store.release() }
    }
    if (!result) throw new Error('Workflow ended without a snapshot')
    return result
  }
  private fail(error: string): void { if (this.snapshot) { this.snapshot.status = 'failed'; this.snapshot.error = error } }
}
