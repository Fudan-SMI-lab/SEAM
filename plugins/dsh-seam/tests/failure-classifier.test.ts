import { test } from 'node:test'
import assert from 'node:assert'
import { classifyFailure } from '../src/failure-classifier.js'

test('classifies device health when only first workload ran', () => {
  const evaluation = {
    executed_count: 1,
    not_run_count: 59,
    total_workloads: 60,
    first_failure_workload: 'w0'
  }

  const classification = classifyFailure(evaluation)

  assert.strictEqual(classification.category, 'device_health')
  assert.ok(classification.confidence > 0.8)
  assert.strictEqual(classification.router_suggestion, 'escalate')
  assert.strictEqual(classification.requires_single_workload_debug, true)
})

test('classifies code bug for valid candidate failure', () => {
  const evaluation = {
    evaluation_valid: true,
    evaluation_scope: 'candidate',
    executed_count: 60,
    not_run_count: 0
  }

  const classification = classifyFailure(evaluation)

  assert.strictEqual(classification.category, 'code_bug')
  assert.strictEqual(classification.router_suggestion, 'fix_operator')
})

test('classifies artifact invalid', () => {
  const evaluation = {
    failure_category: 'artifact_writeback_failed'
  }

  const classification = classifyFailure(evaluation)

  assert.strictEqual(classification.category, 'artifact_invalid')
  assert.strictEqual(classification.router_suggestion, 'reject')
})

test('classifies api timeout for SIGKILL', () => {
  const evaluation = {
    exit_code: -9,
    cleanup_status: 'failed'
  }

  const classification = classifyFailure(evaluation)

  assert.strictEqual(classification.category, 'api_timeout')
  assert.strictEqual(classification.router_suggestion, 'retry_eval')
})

test('classifies device health for memory fault', () => {
  const evaluation = {
    failure_category: 'device_memory_fault',
    timeout_classification: 'device_sync_hang'
  }

  const classification = classifyFailure(evaluation)

  assert.strictEqual(classification.category, 'device_health')
  assert.strictEqual(classification.requires_single_workload_debug, true)
})

test('classifies environment for reference failure', () => {
  const evaluation = {
    evaluation_scope: 'reference',
    evaluation_completeness: 'reference_failed'
  }

  const classification = classifyFailure(evaluation)

  assert.strictEqual(classification.category, 'environment')
  assert.strictEqual(classification.router_suggestion, 'escalate')
})

test('returns unknown for ambiguous cases', () => {
  const evaluation = {
    evaluation_status: 'completed',
    operator_passed: false
  }

  const classification = classifyFailure(evaluation)

  assert.strictEqual(classification.category, 'unknown')
  assert.strictEqual(classification.router_suggestion, 'none')
})
