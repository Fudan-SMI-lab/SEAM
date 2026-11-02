import { randomUUID } from 'node:crypto'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { SessionId } from '@deepseek-ai/dsh-session'
import type { SubagentProvider, ResolvedSubagentStartRequest, SubagentRun } from '@deepseek-ai/dsh-subagent'
import type { Data } from './types.js'

export interface WorkflowCommand { version: 1; token: string; action: string; [key: string]: unknown }

export class WorkflowTransport implements SubagentProvider {
  readonly name = `seam-operations-${randomUUID()}`
  readonly capabilities = { agentOptions: false, outputSchema: false, depthLimit: false, toolFilter: false, persona: false }
  readonly inheritsParentContext = false
  private readonly requests = new Map<string, { parent: Agent; controller: AbortController; pending: Set<Promise<unknown>>; execute(command: WorkflowCommand, signal: AbortSignal): Promise<Data> }>()
  bind(parent: Agent, execute: (command: WorkflowCommand, signal: AbortSignal) => Promise<Data>): { token: string; release(): Promise<void> } {
    const token = randomUUID()
    const owner = { parent, execute, controller: new AbortController(), pending: new Set<Promise<unknown>>() }
    this.requests.set(token, owner)
    return { token, release: async () => {
      this.requests.delete(token)
      owner.controller.abort(new Error('SEAM workflow finished'))
      await Promise.allSettled([...owner.pending])
    } }
  }
  async start(request: ResolvedSubagentStartRequest): Promise<SubagentRun> {
    request.signal.throwIfAborted()
    if (request.agentOptions || request.outputSchema || request.maxDepth !== undefined || request.toolFilter || request.persona) throw new Error('SEAM operation transport does not accept agent options')
    if (request.prompt.length !== 1 || request.prompt[0]?.type !== 'text') throw new Error('SEAM operation requires a single JSON text payload')
    const command = JSON.parse(request.prompt[0].text) as WorkflowCommand
    const owner = this.requests.get(command?.token)
    if (!owner || owner.parent !== request.parent || command.version !== 1 || !['step', 'artifact', 'checkpoint', 'hooks'].includes(command.action)) throw new Error('Invalid or expired SEAM operation capability')
    const controller = new AbortController()
    const signal = AbortSignal.any([request.signal, controller.signal, owner.controller.signal])
    const result = Promise.resolve().then(async () => {
      signal.throwIfAborted()
      const value = await owner.execute(command, signal)
      signal.throwIfAborted()
      return { stopReason: 'completed' as const, output: [{ type: 'text' as const, text: JSON.stringify({ version: 1, ok: true, value }) }] }
    })
    owner.pending.add(result)
    void result.finally(() => owner.pending.delete(result)).catch(() => {})
    return {
      id: SessionId(randomUUID()), localAgent: undefined, result,
      dispose: async () => { controller.abort(new Error('SEAM operation disposed')); await Promise.allSettled([result]) },
    }
  }
}
