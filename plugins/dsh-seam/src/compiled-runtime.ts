import { conditionDecision, pythonString, resolve, truth } from './expressions.js'
import { failureEvidence } from './evidence.js'
import { compactHistoryEntry, summarizeHistory, type CompactHistory } from './history-compactor.js'
import type { Data, Result, Scope } from './types.js'

type AgentCall = (prompt: string, options: { label: string }) => Promise<unknown>

export function createRuntime(context: Data, globals: Data, token: string, agent: AgentCall, phase: (title: string) => void, outputDir: string) {
  const scope: Scope = { state: {}, context, globals, loop_vars: {}, loop_state: {}, loop_history: [], step_outputs: {} }
  let currentPhase = ''
  const rpc = async (action: string, payload: Data): Promise<Data> => {
    const response = await agent(JSON.stringify({ version: 1, token, action, ...payload }), { label: `seam:${action}` })
    if (typeof response !== 'string') throw new Error('SEAM provider did not return JSON text')
    const envelope = JSON.parse(response)
    if (envelope?.version !== 1 || envelope.ok !== true || !envelope.value || typeof envelope.value !== 'object' || Array.isArray(envelope.value)) throw new Error('Invalid SEAM provider response')
    return envelope.value
  }
  const artifact = async (name: string, value: unknown): Promise<void> => { await rpc('artifact', { name, value }) }
  const checkpoint = async (id: string, current: Scope): Promise<void> => {
    currentPhase = id
    await rpc('checkpoint', { currentPhase, state: current.state })
  }
  const matches = async (condition: string, current: Scope, stop = false): Promise<boolean> => {
    const decision = conditionDecision(condition, current, stop)
    if (decision.error) await artifact(stop ? 'stop-condition' : `${currentPhase}-condition`, { condition, ...decision })
    return decision.matched
  }
  return {
    scope, artifact, checkpoint, matches, resolve, truth,
    start: async () => { scope.state = (await rpc('hooks', { point: 'workflow_start', state: scope.state, status: 'running' })).state as Data },
    finish: () => checkpoint(currentPhase, scope),
    enter: async (id: string, title: string, current: Scope) => { phase(title); await checkpoint(id, current) },
    leaf: async (id: string, input: Data, current: Scope): Promise<Result> => await rpc('step', { phaseId: id, input, scope: current }) as unknown as Result,
    record: async (id: string, input: Data, result: Result, current: Scope): Promise<Result> => {
      result.output.framework_execution_status = result.status
      await artifact(id, { phase: id, iteration: current.loop_state.iteration, input, result })
      return result
    },
    route: (value: unknown, choices: Record<string, string>): Result => {
      const key = value && typeof value === 'object' && !Array.isArray(value) ? Object.hasOwn(value, 'value') ? (value as Data).value : Object.hasOwn(value, 'role') ? (value as Data).role : '' : value
      const route = pythonString(key)
      const next = Object.hasOwn(choices, route) ? choices[route] : undefined
      return next ? { status: 'success', output: { route, target: next }, next } : { status: 'failure', output: { failure_kind: 'invalid_dispatch_route', route, available_routes: Object.keys(choices) } }
    },
    loopScope: (parent: Scope, variables: Data): Scope => ({ ...parent, loop_vars: variables, loop_state: { iteration: 0, stagnation_count: 0, last_error_signature: '', review_reject_count: 0, review_gate_enabled: false, operator_passed: false, evaluation_status: 'not_run' }, step_outputs: {}, loop_history: [] }),
    promote: (current: Scope, id: string, alias: string, result: Result, fields: string[]) => {
      current.step_outputs[id] = result.output
      current.step_outputs[alias] = result.output
      current.loop_state[id] = result.output
      current.loop_state[alias] = result.output
      current.state[alias] = result.output
      for (const field of fields) if (Object.hasOwn(result.output, field)) {
        current.loop_state[field] = result.output[field]
        current.step_outputs[field] = result.output[field]
      }
    },
    remember: (current: Scope, iteration: number, status: Result['status']) => {
      const compact = compactHistoryEntry(iteration, status, current.step_outputs, outputDir) as unknown as Data
      current.loop_history.push(compact)
      current.loop_history = summarizeHistory(current.loop_history as unknown as CompactHistory[], 5) as unknown as Data[]
    },
    stagnates: (current: Scope, threshold: number): boolean => {
      const signature = failureEvidence(current.loop_state).split('\n').map(line => line.trimEnd()).join('\n')
      current.loop_state.stagnation_count = signature && signature === current.loop_state.last_error_signature ? Number(current.loop_state.stagnation_count) + 1 : 1
      current.loop_state.last_error_signature = signature
      return Number(current.loop_state.stagnation_count) >= threshold
    },
    loopResult: (current: Scope, status: Result['status'], loopStatus?: string): Result => ({ status, output: { ...current.loop_state, ...(loopStatus ? { loop_status: loopStatus } : {}), history: current.loop_history } }),
  }
}
