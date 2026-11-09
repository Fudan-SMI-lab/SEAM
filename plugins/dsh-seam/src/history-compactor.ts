import type { Data } from './types.js'

export interface CompactHistory {
  iteration: number
  status: string
  key_fields: Data
  artifact_refs: Record<string, string>
}

const PRESERVED_FIELDS = [
  'evaluation_status', 'operator_passed', 'failure_category', 'failure_kind',
  'evaluation_scope', 'evaluation_completeness', 'first_failure_workload',
  'exit_code', 'timeout_classification', 'total_workloads', 'passed_workloads',
  'solution_sha256', 'embedded_source_sha256', 'repair_role',
  'requires_single_workload_debug', 'executed_count', 'not_run_count',
  'evaluation_valid', 'trace_complete', 'cascade_failure_count'
]

const LARGE_TEXT_FIELDS = [
  'operator_evaluation_text', 'operator_stderr_text', 'operator_stdout_text',
  'operator_error_analysis_text', 'raw_response', 'validation_errors',
  'operator_definition_text', 'operator_workload_text', 'operator_analysis_text'
]

export function compactHistoryEntry(
  iteration: number,
  status: string,
  step_outputs: Data,
  outputDir: string
): CompactHistory {
  const key_fields: Data = {}
  const artifact_refs: Record<string, string> = {}

  // 保留关键字段
  for (const field of PRESERVED_FIELDS) {
    if (Object.hasOwn(step_outputs, field)) {
      key_fields[field] = step_outputs[field]
    }
  }

  // 大文本改为引用
  for (const field of LARGE_TEXT_FIELDS) {
    if (step_outputs[field] && typeof step_outputs[field] === 'string') {
      const text = step_outputs[field] as string
      if (text.length > 500) {
        artifact_refs[field] = `${outputDir}/iteration-${iteration}/${field}.txt`
      } else {
        // 短文本保留
        key_fields[field] = text
      }
    }
  }

  return { iteration, status, key_fields, artifact_refs }
}

export function summarizeHistory(
  history: CompactHistory[],
  maxEntries = 5
): CompactHistory[] {
  if (history.length <= maxEntries) return history

  // 保留第一轮 + 最近 maxEntries-1 轮
  const recent = history.slice(-(maxEntries - 1))
  const first = history[0]

  if (!first || !recent[0]) return history

  return first.iteration === recent[0].iteration ? recent : [first, ...recent]
}
