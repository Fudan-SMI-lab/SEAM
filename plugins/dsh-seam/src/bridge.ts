import { join } from 'node:path'
import { runProcess } from './process.js'
import type { Data, Execution, Handler, Result } from './types.js'

export const operatorOperations = new Set([
  'operator_task_validate', 'operator_runtime_detect', 'operator_runtime_configure',
  'operator_artifact_initialize', 'operator_artifact_pack', 'operator_artifact_validate',
  'operator_kernelbench_eval', 'operator_evaluation_classify', 'operator_trace_summary',
])

export const supportedValidators = new Set([
  'constraint_summary', 'entry_script', 'entry_static', 'env_detect', 'operator_analysis',
  'operator_env_detect', 'operator_evaluation', 'operator_report', 'operator_runtime_configure',
  'operator_runtime_detect', 'project_analysis', 'repair_classification', 'reports', 'rule_migration', 'validation_final', 'venv',
])

export class DomainBridge {
  constructor(private readonly options: { repository: string; python: string; outputDir: string; maxPromptBytes: number; agentSkills?: Record<string, Data> }) {}
  private async call(action: string, execution: Execution, extra: Data = {}): Promise<Data> {
    const output = await runProcess(this.options.python, [join(this.options.repository, 'plugins/dsh-seam/python/bridge.py')], {
      cwd: execution.workspace, signal: execution.signal,
      input: { action, execution: { ...execution, signal: undefined }, output_dir: this.options.outputDir, max_prompt_bytes: this.options.maxPromptBytes, ...extra },
    })
    return JSON.parse(output) as Data
  }
  builtin: Handler = async execution => {
    const operation = execution.phase.operation ?? execution.phase.params?.operation
    if (!operatorOperations.has(String(operation))) throw new Error(`Builtin not migrated: ${String(operation)}`)
    return await this.call('builtin', execution) as unknown as Result
  }
  async render(execution: Execution): Promise<string> {
    const response = await this.call('render', execution, { agent_skills: this.options.agentSkills?.[execution.phase.agent ?? ''] ?? {} })
    if (typeof response.prompt !== 'string') throw new Error('Prompt bridge returned no prompt')
    return response.prompt
  }
  async validate(execution: Execution, result: Result): Promise<Result> {
    if (result.status !== 'success') return result
    return await this.call('validate', execution, { result }) as unknown as Result
  }
}
