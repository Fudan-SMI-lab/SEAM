import type { Context } from '@deepseek-ai/cordis'
import { brandString } from '@deepseek-ai/dsh-brand'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type { SessionId } from '@deepseek-ai/dsh-session'
import type {} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-tools'
import type {} from '@deepseek-ai/dsh-workflow'
import type {} from '@deepseek-ai/dsh-subagent'
import { WorkflowTransport } from './workflow-transport.js'
import { SeamController, type ControllerConfig } from './controller.js'
import type { DshHost, DshHandle } from './dsh-runtime.js'
import type { Data } from './types.js'

export const name = 'seam-workflow'
export const inject = ['agents', 'sessions', 'tools', 'workflowEngine', 'subagents']

export function apply(ctx: Context, config: ControllerConfig): void {
  const agents = ctx.get('agents')!
  const sessions = ctx.get('sessions')!
  const host: DshHost = {
    agents: {
      create: async options => await agents.create({ ...options, sessionId: brandString<SessionId>(options.sessionId) }) as unknown as DshHandle,
      resume: async options => await agents.resume({ ...options, resumeSessionId: brandString<SessionId>(options.resumeSessionId) }) as unknown as DshHandle,
    },
    message: text => createUserMessage({ content: [{ type: 'text', text }], source: { kind: 'user' } }),
    flush: async agent => {
      const session = sessions.get(brandString<SessionId>(agent.id))
      if (!session || !await sessions.flush(session)) throw new Error('Session persistence is required for chat restoration')
    },
  }
  const transport = new WorkflowTransport()
  const controller = new SeamController(host, config, ctx.workflowEngine, transport)
  const tools = ctx.get('tools')!
  ctx.effect(() => {
    const removeProvider = ctx.subagents.registerProvider(transport)
    let removeService: (() => void) | undefined
    let removeTool: (() => void) | undefined
    try {
      removeService = ctx.provide('seamWorkflow', controller)
      removeTool = tools.register({
        name: 'seam_workflow', description: 'Start or inspect a configured SEAM workflow; restore_chat restores role chats only, never workflow execution.',
        parameters: { type: 'object', required: ['action', 'runId'], additionalProperties: false, properties: { action: { type: 'string', enum: ['run', 'status', 'cancel', 'restore_chat', 'close_chat'] }, runId: { type: 'string' }, workflow: { type: 'string' }, workspace: { type: 'string' }, context: { type: 'object', additionalProperties: true } } },
        output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
        execute: async (input: unknown, exec) => {
          const args = input as Data
          if (typeof args.runId !== 'string') throw new Error('runId is required')
          switch (args.action) {
            case 'run':
              if (typeof args.workflow !== 'string' || typeof args.workspace !== 'string' || !args.context || typeof args.context !== 'object' || Array.isArray(args.context)) throw new Error('run requires workflow, workspace and context')
              if (!exec.agent) throw new Error('seam_workflow run requires a calling dsh Agent')
              return controller.run({ runId: args.runId, workflow: args.workflow, workspace: args.workspace, context: args.context as Data }, exec.agent)
            case 'status': return controller.status(args.runId)
            case 'cancel': return controller.cancel(args.runId)
            case 'restore_chat': return { sessions: await controller.restoreChat(args.runId) }
            case 'close_chat': await controller.closeChat(args.runId); return { status: 'closed' }
            default: throw new Error('Unknown SEAM control action')
          }
        },
      })
    } catch (error) { removeService?.(); removeProvider(); throw error }
    return async () => { removeTool?.(); removeService?.(); try { await controller.dispose() } finally { removeProvider() } }
  }, 'seam.workflow')
}
