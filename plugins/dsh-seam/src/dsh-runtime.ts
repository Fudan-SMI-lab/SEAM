import { randomUUID } from 'node:crypto'
import { realpath } from 'node:fs/promises'
import type { Context } from '@deepseek-ai/cordis'
import type { ContainerExecutionBackend } from './execution-backend.js'
import { createContainerBashTool } from './container-tool.js'
import type { Data, Execution, Result, Runtime } from './types.js'

export interface DshEvent { type: string; data: Data }
export interface DshAgent {
  id: string
  status: string
  session: { header: { cwd?: string }; snapshotEvents(): readonly DshEvent[] }
  followup(message: { id: string; [key: string]: unknown }): void
  whenIdle(): Promise<void>
  cancel(cause: { kind: 'user' | 'parent' | 'hook'; reason?: string }): void
}
export interface DshHandle { agent: DshAgent; dispose(): Promise<void> }
export type AgentScope = Context
export interface DshHost {
  agents: {
    create(options: { sessionId: string; meta: { cwd: string }; signal: AbortSignal; setup: (scope: AgentScope) => void; agentOptions?: Data }): Promise<DshHandle>
    resume(options: { resumeSessionId: string; signal: AbortSignal; setup: (scope: AgentScope) => void; agentOptions?: Data }): Promise<DshHandle>
  }
  message(text: string): { id: string; [key: string]: unknown }
  flush(agent: DshAgent): Promise<void>
}
export interface RuntimeOptions {
  host: DshHost
  workspace: string
  roleTools: Record<string, readonly string[]>
  roleOptions?: Record<string, Data>
  lifecycle?: Record<string, string>
  resumeSessions?: Record<string, string>
  containerBackend?: ContainerExecutionBackend
  render(execution: Execution): Promise<string>
  onSessions?(sessions: Record<string, string>): Promise<void>
  onEvents?(phase: string, events: readonly DshEvent[]): Promise<void>
}

export class DshRuntime implements Runtime {
  private readonly handles = new Map<string, DshHandle>()
  private readonly identities: Record<string, string>
  private readonly busy = new Set<string>()
  constructor(private readonly options: RuntimeOptions) {
    this.identities = { ...options.resumeSessions }
  }
  sessions(): Record<string, string> { return { ...this.identities } }
  async restore(signal: AbortSignal): Promise<Record<string, string>> {
    try {
      for (const role of Object.keys(this.identities)) await this.handle(role, signal)
      return this.sessions()
    } catch (error) { await this.dispose(); throw error }
  }
  private async handle(role: string, signal: AbortSignal): Promise<DshHandle> {
    const cached = this.handles.get(role)
    if (cached) return cached
    const allow = this.options.roleTools[role]
    if (!allow) throw new Error(`An explicit tool policy is required for ${role}`)
    const workspace = await realpath(this.options.workspace)
    const setup = (scope: AgentScope): void => {
      if (this.options.containerBackend && allow.includes('bash')) scope.tools.register(createContainerBashTool(this.options.containerBackend, workspace))
      scope.tools.restrict({ allow })
    }
    const prior = this.identities[role]
    const handle = prior
      ? await this.options.host.agents.resume({ resumeSessionId: prior, signal, setup, agentOptions: this.options.roleOptions?.[role] })
      : await this.options.host.agents.create({ sessionId: randomUUID(), meta: { cwd: workspace }, signal, setup, agentOptions: this.options.roleOptions?.[role] })
    try {
      if (!handle.agent.session.header.cwd || await realpath(handle.agent.session.header.cwd) !== workspace) throw new Error('Resumed session workspace does not match task workspace')
      if (handle.agent.status !== 'idle') throw new Error('Role session is not idle')
      this.handles.set(role, handle)
      this.identities[role] = handle.agent.id
      await this.options.onSessions?.(this.sessions())
      return handle
    } catch (error) {
      this.handles.delete(role)
      if (prior) this.identities[role] = prior
      else delete this.identities[role]
      await handle.dispose()
      throw error
    }
  }
  async execute(execution: Execution): Promise<Result> {
    const role = execution.phase.agent
    if (!role) throw new Error('An LLM phase needs a role')
    if (this.busy.has(role)) throw new Error(`Concurrent request for role ${role}`)
    this.busy.add(role)
    let handle: DshHandle | undefined
    try {
      const text = execution.correction ?? await this.options.render(execution)
      execution.signal.throwIfAborted()
      handle = await this.handle(role, execution.signal)
      const { agent } = handle
      const start = agent.session.snapshotEvents().length
      const message = this.options.host.message(text)
      const cancel = (): void => agent.cancel({ kind: 'hook', reason: String(execution.signal.reason ?? 'Phase cancelled') })
      execution.signal.addEventListener('abort', cancel, { once: true })
      try {
        execution.signal.throwIfAborted()
        agent.followup(message)
        await agent.whenIdle()
        await this.options.host.flush(agent)
        const events = agent.session.snapshotEvents().slice(start)
        await this.options.onEvents?.(execution.phase.id, events)
        execution.signal.throwIfAborted()
        let turn: unknown
        let active: unknown
        for (const event of events) {
          if (event.type === 'turn/start') active = event.data.turn
          if (event.type === 'user/message' && event.data.id === message.id) turn = active
        }
        if (turn === undefined) throw new Error('dsh did not claim the submitted message')
        const end = events.find(event => event.type === 'turn/end' && event.data.turn === turn)
        const reason = end?.data.reason as Data | undefined
        if (reason?.kind !== 'completed') return { status: 'failure', output: { failure_kind: 'runtime_turn_failed', session_id: agent.id, reason, events } }
        const messages = events.filter(event => event.type === 'assistant/message' && event.data.turn === turn && !event.data.interrupted)
        const finalMessage = messages.at(-1)?.data.message as { content?: { type: string; text?: string }[] } | undefined
        const raw = finalMessage?.content?.filter(block => block.type === 'text').map(block => block.text ?? '').join('') ?? ''
        const fenced = /^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/.exec(raw.trim())
        let output: unknown
        try { output = JSON.parse(fenced?.[1] ?? raw) }
        catch { return { status: 'failure', output: { failure_kind: 'invalid_json_output', raw_response: raw, session_id: agent.id } } }
        if (!output || typeof output !== 'object' || Array.isArray(output)) return { status: 'failure', output: { failure_kind: 'invalid_output_shape', raw_response: raw } }
        return { status: 'success', output: output as Data }
      } finally { execution.signal.removeEventListener('abort', cancel) }
    } finally {
      try {
        if (handle && this.options.lifecycle?.[role] === 'ephemeral') {
          try { await handle.dispose() }
          finally { this.handles.delete(role); delete this.identities[role] }
        }
      } finally { this.busy.delete(role) }
    }
  }
  async dispose(): Promise<void> {
    const handles = [...this.handles.values()]
    this.handles.clear()
    const results = await Promise.allSettled(handles.map(async handle => {
      handle.agent.cancel({ kind: 'parent' })
      try {
        await handle.agent.whenIdle()
        await this.options.host.flush(handle.agent)
      } finally { await handle.dispose() }
    }))
    const failures = results.filter(result => result.status === 'rejected')
    if (failures.length) throw new AggregateError(failures.map(result => (result as PromiseRejectedResult).reason), 'Role session cleanup failed')
  }
}
