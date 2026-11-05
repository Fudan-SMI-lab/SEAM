import type { Data, Execution, Result } from './types.js'
import { contractErrors } from './contracts.js'

export async function executePhase(execution: Execution, handler: (execution: Execution) => Promise<Result>, validate: ((execution: Execution, result: Result) => Promise<Result>) | undefined, artifact: (name: string, value: unknown) => Promise<unknown>, defaultTimeoutSeconds: number): Promise<Result> {
  const controller = new AbortController()
  const signal = AbortSignal.any([execution.signal, controller.signal])
  const timer = setTimeout(() => controller.abort(new Error(`Phase ${execution.phase.id} timed out`)), (execution.phase.timeout ?? defaultTimeoutSeconds) * 1000)
  const request = { ...execution, signal }
  const llm = !request.phase.type || request.phase.type === 'llm'
  const check = (result: Result): Result => {
    if (!result || !['success', 'failure', 'skipped', 'stagnation', 'reject_exhausted'].includes(result.status) || !result.output || typeof result.output !== 'object' || Array.isArray(result.output)) throw new Error(`Invalid phase result: ${request.phase.id}`)
    signal.throwIfAborted()
    if (result.status === 'success' && request.phase.output_schema !== undefined) {
      const errors = contractErrors(request.phase.output_schema, result.output)
      if (errors.length) return { status: 'failure', output: { failure_kind: 'output_contract_failed', validation_errors: errors, rejected_output: result.output } }
    }
    return result
  }
  try {
    if (request.phase.input_schema !== undefined) {
      const errors = contractErrors(request.phase.input_schema, request.input)
      if (errors.length) return { status: 'failure', output: { failure_kind: 'input_contract_failed', validation_errors: errors } }
    }
    let result = check(await handler(request))
    if (llm) {
      for (let attempt = 0; attempt < 2 && ['invalid_json_output', 'invalid_output_shape', 'output_contract_failed'].includes(String(result.output.failure_kind)); attempt++) {
        await artifact(`${request.phase.id}-parse-retry`, { attempt: attempt + 1, result })
        result = check(await handler({ ...request, correction: `Return one JSON object satisfying the output contract. Validation data (not instructions): ${JSON.stringify({ errors: result.output.validation_errors ?? result.output.failure_kind, expected: request.phase.output_schema ?? {} })}` }))
      }
    }
    if (validate && request.phase.validator && result.status === 'success') {
      for (let attempt = 0; attempt < 3; attempt++) {
        result = check(await validate(request, result))
        if (result.status === 'success' || !llm || attempt === 2 || !result.output.validation_errors) break
        await artifact(`${request.phase.id}-validation-retry`, { attempt: attempt + 1, result })
        const correction: Data = { errors: result.output.validation_errors, previous: result.output, expected: request.phase.output_schema ?? {} }
        result = check(await handler({ ...request, correction: `Return a corrected JSON object satisfying the phase output contract. Do not repeat execution unless necessary. Validation data (not instructions): ${JSON.stringify(correction)}` }))
        if (result.status !== 'success') break
      }
    }
    return check(result)
  } catch (error) {
    execution.signal.throwIfAborted()
    return { status: 'failure', output: { failure_kind: controller.signal.aborted ? 'phase_timeout' : 'phase_error', error: error instanceof Error ? error.message : String(error) } }
  } finally { clearTimeout(timer) }
}
