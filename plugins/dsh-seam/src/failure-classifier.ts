import type { Data } from './types.js'

export interface FailureClassification {
  category: 'device_health' | 'api_timeout' | 'artifact_invalid' | 'code_bug' | 'environment' | 'unknown'
  confidence: number
  evidence: string[]
  router_suggestion: 'fix_operator' | 'retry_eval' | 'escalate' | 'reject' | 'none'
  requires_single_workload_debug: boolean
}

export function classifyFailure(evaluation: Data): FailureClassification {
  // Rule 1: Artifact problems
  if (evaluation.failure_category === 'artifact_writeback_failed' ||
      evaluation.failure_category === 'candidate_invalid') {
    return {
      category: 'artifact_invalid',
      confidence: 0.95,
      evidence: ['failure_category=' + evaluation.failure_category],
      router_suggestion: 'reject',
      requires_single_workload_debug: false
    }
  }

  // Rule 2: Device problem - only first workload executed
  const executed = typeof evaluation.executed_count === 'number' ? evaluation.executed_count : (typeof evaluation.total_workloads === 'number' ? evaluation.total_workloads : 0)
  const notRun = typeof evaluation.not_run_count === 'number' ? evaluation.not_run_count : 0
  if (executed === 1 && notRun > 50) {
    return {
      category: 'device_health',
      confidence: 0.9,
      evidence: [
        `executed_count=1`,
        `not_run_count=${notRun}`,
        `first_failure=${evaluation.first_failure_workload || 'unknown'}`
      ],
      router_suggestion: 'escalate',
      requires_single_workload_debug: true
    }
  }

  // Rule 3: Process killed / SIGKILL
  if (evaluation.exit_code === -9 || evaluation.cleanup_status === 'failed') {
    return {
      category: 'api_timeout',
      confidence: 0.7,
      evidence: [`exit_code=${evaluation.exit_code}`, `cleanup_status=${evaluation.cleanup_status}`],
      router_suggestion: 'retry_eval',
      requires_single_workload_debug: false
    }
  }

  // Rule 4: Device fault markers
  const deviceMarkers = ['device_memory_fault', 'device_sync_hang', 'host_crash']
  if (deviceMarkers.includes(evaluation.timeout_classification as string) ||
      deviceMarkers.includes(evaluation.failure_category as string)) {
    return {
      category: 'device_health',
      confidence: 0.85,
      evidence: [
        `timeout_classification=${evaluation.timeout_classification}`,
        `failure_category=${evaluation.failure_category}`
      ],
      router_suggestion: 'fix_operator',
      requires_single_workload_debug: true
    }
  }

  // Rule 5: Reference failure
  if (evaluation.evaluation_scope === 'reference' ||
      evaluation.evaluation_completeness === 'reference_failed') {
    return {
      category: 'environment',
      confidence: 0.9,
      evidence: [`evaluation_scope=${evaluation.evaluation_scope}`],
      router_suggestion: 'escalate',
      requires_single_workload_debug: false
    }
  }

  // Rule 6: Normal candidate failure
  if (evaluation.evaluation_scope === 'candidate' &&
      evaluation.evaluation_valid === true) {
    return {
      category: 'code_bug',
      confidence: 0.8,
      evidence: ['evaluation_valid=true', 'evaluation_scope=candidate'],
      router_suggestion: 'fix_operator',
      requires_single_workload_debug: false
    }
  }

  // Default
  return {
    category: 'unknown',
    confidence: 0.3,
    evidence: ['no_matching_rule'],
    router_suggestion: 'none',
    requires_single_workload_debug: false
  }
}
