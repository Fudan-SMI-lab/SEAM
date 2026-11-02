export type Data = Record<string, unknown>
export type Outcome = 'success' | 'failure' | 'skipped' | 'stagnation' | 'reject_exhausted'
export interface Result {
  status: Outcome
  output: Data
  next?: string
}
export interface Phase {
  id: string
  name?: string
  type?: string
  agent?: string
  operation?: string
  handler?: string
  prompt_template?: string
  validator?: string
  timeout?: number
  condition?: string
  input_mapping?: Data
  params?: Data
  output_as?: string
  sub_workflow?: string
  on_failure?: string
  transitions?: Record<string, string>
  command?: string | string[]
  cwd?: string
  env?: Record<string, string>
  capture?: Record<string, string>
  runtime_skills?: { include?: string[]; inject_full?: boolean; missing?: string }
  [key: string]: unknown
}
export interface Loop {
  id?: string
  max_iterations: number | string
  stagnation_threshold?: number | string
  review_gate_enabled?: boolean | string
  stop_conditions?: { condition: string; status: string }[]
  phases: Phase[]
  blocks?: Record<string, { phases?: Phase[] }>
}
export interface Workflow {
  name: string
  version: string | number
  globals?: Data
  agents?: Record<string, { role?: string; lifecycle?: string }>
  terminals?: Record<string, string> | string[]
  phases: Phase[]
  sub_workflows?: Record<string, Loop>
  hooks?: Record<string, { type?: string; operation: string; save_as?: string; critical?: boolean; params?: Data }[]>
  [key: string]: unknown
}
export interface Scope {
  state: Data
  context: Data
  globals: Data
  loop_vars: Data
  loop_state: Data
  loop_history: Data[]
  step_outputs: Data
}
export interface Execution {
  runId: string
  workspace: string
  phase: Phase
  input: Data
  scope: Scope
  signal: AbortSignal
  correction?: string
}
export type Handler = (execution: Execution) => Promise<Result>
export interface Runtime {
  execute(execution: Execution): Promise<Result>
  sessions(): Record<string, string>
  dispose(): Promise<void>
}
export interface RunSnapshot {
  format: 1
  runId: string
  workflow: string
  workflowHash: string
  workspace: string
  status: 'running' | 'complete' | 'failed' | 'blocked' | 'cancelled'
  currentPhase?: string
  terminal?: string
  state: Data
  context: Data
  sessions: Record<string, string>
  executionEngine?: 'dsh-workflow'
  dshWorkflowId?: string
  error?: string
}
