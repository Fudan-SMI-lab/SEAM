import { test } from 'node:test'
import assert from 'node:assert'
import { compactHistoryEntry, summarizeHistory } from '../src/history-compactor.js'

test('compactHistoryEntry preserves key fields', () => {
  const stepOutputs = {
    evaluation_status: 'failed',
    operator_passed: false,
    failure_category: 'device_memory_fault',
    total_workloads: 60,
    passed_workloads: 0,
    operator_stderr_text: 'x'.repeat(7000),
    operator_evaluation_text: 'y'.repeat(7000)
  }
  const compact = compactHistoryEntry(3, 'failure', stepOutputs, '/tmp/output')

  assert.strictEqual(compact.iteration, 3)
  assert.strictEqual(compact.status, 'failure')
  assert.strictEqual(compact.key_fields.evaluation_status, 'failed')
  assert.strictEqual(compact.key_fields.operator_passed, false)
  assert.strictEqual(compact.key_fields.failure_category, 'device_memory_fault')
  assert.ok(compact.artifact_refs.operator_stderr_text)
  assert.ok(compact.artifact_refs.operator_evaluation_text)
  assert.ok(!compact.key_fields.operator_stderr_text, 'Large text should not be in key_fields')
})

test('compactHistoryEntry keeps short text in key_fields', () => {
  const stepOutputs = {
    evaluation_status: 'failed',
    operator_stderr_text: 'Short error message'
  }
  const compact = compactHistoryEntry(1, 'failure', stepOutputs, '/tmp/output')

  assert.strictEqual(compact.key_fields.operator_stderr_text, 'Short error message')
  assert.ok(!compact.artifact_refs.operator_stderr_text, 'Short text should stay in key_fields')
})

test('summarizeHistory keeps recent + first when over limit', () => {
  const history = Array.from({length: 10}, (_, i) => ({
    iteration: i,
    status: 'failure',
    key_fields: { evaluation_status: 'failed' },
    artifact_refs: {}
  }))
  const summarized = summarizeHistory(history, 5)

  assert.strictEqual(summarized.length, 5)
  assert.strictEqual(summarized[0].iteration, 0, 'Should keep first iteration')
  assert.strictEqual(summarized[1].iteration, 6, 'Should keep recent iterations')
  assert.strictEqual(summarized[4].iteration, 9, 'Should keep last iteration')
})

test('summarizeHistory returns original when under limit', () => {
  const history = Array.from({length: 3}, (_, i) => ({
    iteration: i,
    status: 'success',
    key_fields: {},
    artifact_refs: {}
  }))
  const summarized = summarizeHistory(history, 5)

  assert.strictEqual(summarized.length, 3)
  assert.deepStrictEqual(summarized, history)
})

test('summarizeHistory handles edge case when first is in recent window', () => {
  const history = Array.from({length: 4}, (_, i) => ({
    iteration: i,
    status: 'failure',
    key_fields: {},
    artifact_refs: {}
  }))
  const summarized = summarizeHistory(history, 5)

  // All 4 entries fit in window of 5
  assert.strictEqual(summarized.length, 4)
})
