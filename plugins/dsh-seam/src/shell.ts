import { resolve as resolvePath } from 'node:path'
import { resolve } from './expressions.js'
import { LocalExecutionBackend, type ExecutionBackend } from './execution-backend.js'
import type { Data, Execution, Handler, Result } from './types.js'

function stringValue(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.length) throw new Error(`Shell phase ${field} must resolve to a non-empty string`)
  return value
}

export const createShellPhase = (backend: ExecutionBackend): Handler => async (execution: Execution): Promise<Result> => {
  const phase = execution.phase
  if (phase.command === undefined) throw new Error(`Shell phase ${phase.id} has no command`)
  const command = resolve(phase.command, execution.scope)
  if (typeof command !== 'string' && (!Array.isArray(command) || command.some(part => typeof part !== 'string'))) throw new Error(`Shell phase ${phase.id} command must be a string or string array`)
  const cwdValue = phase.cwd === undefined ? execution.workspace : resolve(phase.cwd, execution.scope)
  const cwd = resolvePath(execution.workspace, stringValue(cwdValue, 'cwd'))
  const rawEnv = phase.env === undefined ? {} : resolve(phase.env, execution.scope)
  if (!rawEnv || typeof rawEnv !== 'object' || Array.isArray(rawEnv)) throw new Error(`Shell phase ${phase.id} env must resolve to a mapping`)
  const env: Record<string, string> = {}
  for (const [key, value] of Object.entries(rawEnv)) {
    if (typeof value !== 'string') throw new Error(`Shell phase ${phase.id} environment variable ${key} must resolve to a string`)
    env[key] = value
  }
  const result = await backend.run(command as string | string[], { cwd, env, signal: execution.signal, timeoutMs: phase.timeout ? phase.timeout * 1000 : undefined })
  const output: Data = { ...result }
  for (const [source, destination] of Object.entries(phase.capture ?? {})) {
    if (!['exit_code', 'stdout', 'stderr', 'duration'].includes(source)) throw new Error(`Unsupported shell capture source: ${source}`)
    output[destination] = result[source as keyof typeof result]
  }
  return { status: result.exit_code === 0 ? 'success' : 'failure', output }
}

export const shellPhase: Handler = createShellPhase(new LocalExecutionBackend())
