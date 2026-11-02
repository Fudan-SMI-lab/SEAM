import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { DomainBridge } from '../src/bridge.js'
import { failureEvidence } from '../src/evidence.js'
import type { Data, Execution } from '../src/types.js'

const repository = fileURLToPath(new URL('../../../', import.meta.url))
const python = process.env.SEAM_TEST_PYTHON ?? 'python3'

async function fixture(context: { after: (callback: () => Promise<void>) => void }) {
  const root = await mkdtemp(join(tmpdir(), 'seam-bridge-'))
  context.after(() => rm(root, { recursive: true, force: true }))
  await mkdir(join(root, 'sources'))
  await writeFile(join(root, 'sources/adapter.py'), 'def run(value):\n    return value\n')
  await writeFile(join(root, 'solution_spec.json'), JSON.stringify({ name: 'candidate', entry_point: 'sources/adapter.py::run', sources: [{ path: 'sources/adapter.py', language: 'python' }] }))
  await writeFile(join(root, 'definition.json'), JSON.stringify({ name: 'candidate', reference: 'def run(value):\n    return value\n' }))
  await writeFile(join(root, 'workload.jsonl'), '{"shape":[16]}\n')
  const bridge = new DomainBridge({ repository, python, outputDir: root, maxPromptBytes: 200_000 })
  const execution: Execution = {
    runId: 'fixture', workspace: root, phase: { id: 'fixture', type: 'builtin' }, input: {}, signal: new AbortController().signal,
    scope: { state: {}, context: { OPERATOR_TASK_DIR: root, OPERATOR_SOLUTION_PATH: join(root, 'solution.json') }, globals: {}, loop_vars: {}, loop_state: {}, loop_history: [], step_outputs: {} },
  }
  const call = (operation: string, params: Data = {}) => bridge.builtin({ ...execution, phase: { id: 'fixture', type: 'builtin', operation, params } })
  return { root, bridge, execution, call }
}

test('domain bridge packages repaired sources; failed producers cannot publish', async context => {
  const { root, execution, call } = await fixture(context)
  assert.equal((await call('operator_task_validate')).status, 'success')
  const first = await call('operator_artifact_pack')
  assert.equal(first.status, 'success')
  const before = await readFile(join(root, 'solution.json'), 'utf8')
  const repaired = 'def run(value):\n    return value + 1\n'
  await writeFile(join(root, 'sources/adapter.py'), repaired)
  execution.scope.loop_state.repair = { framework_execution_status: 'failure' }
  assert.equal((await call('operator_artifact_pack', { producer: 'repair' })).status, 'failure')
  assert.equal(await readFile(join(root, 'solution.json'), 'utf8'), before)
  execution.scope.loop_state.repair = { framework_execution_status: 'success' }
  const second = await call('operator_artifact_pack', { producer: 'repair' })
  assert.equal(second.status, 'success')
  assert.notEqual(second.output.solution_sha256, first.output.solution_sha256)
  assert.equal(JSON.parse(await readFile(join(root, 'solution.json'), 'utf8')).sources[0].content, repaired)
  assert.equal((await call('operator_artifact_validate')).output.solution_ready, true)
})

test('invalid publication preserves candidate and blocks initialize/evaluation', async context => {
  const { root, execution, call } = await fixture(context)
  assert.equal((await call('operator_artifact_pack')).status, 'success')
  const before = await readFile(join(root, 'solution.json'), 'utf8')
  await writeFile(join(root, 'solution_spec.json'), JSON.stringify({ sources: [{ path: '../outside.cpp' }] }))
  const failure = await call('operator_artifact_pack')
  assert.equal(failure.status, 'failure')
  assert.equal(await readFile(join(root, 'solution.json'), 'utf8'), before)
  execution.scope.loop_state.operator_artifact_pack = failure.output
  assert.equal((await call('operator_artifact_initialize')).status, 'failure')
  const blocked = await call('operator_kernelbench_eval')
  assert.equal(blocked.status, 'failure')
  assert.equal(blocked.output.failure_category, 'artifact_writeback_failed')
  assert.equal(blocked.output.evaluation_valid, false)
})

test('bridge rejects missing prerequisites before hardware evaluation', async context => {
  const { execution, call } = await fixture(context)
  assert.equal((await call('operator_runtime_detect')).status, 'failure')
  assert.equal((await call('operator_trace_summary')).status, 'failure')
  execution.scope.context.OPERATOR_SOLUTION_PATH = ''
  assert.equal((await call('operator_artifact_validate')).status, 'failure')
  assert.equal((await call('operator_artifact_pack')).status, 'failure')
  execution.scope.context.OPERATOR_TASK_DIR = ''
  await assert.rejects(call('operator_task_validate'), /OPERATOR_TASK_DIR is required/)
})

test('evaluation cannot consume missing or stale candidates', async context => {
  const { execution, call } = await fixture(context)
  assert.equal((await call('operator_kernelbench_eval')).output.failure_category, 'candidate_missing')
  execution.scope.loop_state.operator_artifact_state = { stale_candidate: true, candidate_evaluable: false }
  assert.equal((await call('operator_kernelbench_eval')).output.failure_category, 'stale_candidate')
})

test('prompt bundles round artifacts and suggestions together, with an explicit size failure', async context => {
  const { root, bridge, execution } = await fixture(context)
  execution.phase = { id: 'repair', agent: 'fixer', prompt_template: 'repair_operator_fixer_kernelbench_ascendc' }
  execution.scope.loop_history = [{ iteration: 1, outputs: { artifact: { sha256: 'fixture-sha' }, error_analysis: { recommendation: 'inspect the actual source types' } } }]
  const prompt = await bridge.render(execution)
  assert.ok(prompt.includes('fixture-sha'))
  assert.ok(prompt.includes('inspect the actual source types'))
  const limited = new DomainBridge({ repository, python, outputDir: root, maxPromptBytes: 10 })
  await assert.rejects(limited.render(execution), /exceeds max_prompt_bytes/)
})

test('project prompt rendering receives generic phase, project, mapped input and prior state', async context => {
  const { bridge, execution } = await fixture(context)
  execution.phase = { id: 'phase_0_env_detect', name: 'Environment detection', prompt_template: 'phase_0_env_detect', type: 'llm' }
  execution.scope.context.PROJECT_DIR = execution.workspace
  execution.scope.state.previous = { result: 'prior phase' }
  execution.input.extra_context = 'mapped value'
  const prompt = await bridge.render(execution)
  assert.match(prompt, /Environment detection/)
  assert.match(prompt, new RegExp(execution.workspace.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.match(prompt, /mapped value/)
  assert.match(prompt, /prior phase/)
})

test('project repair classification validator uses the fixed repair-role contract', async context => {
  const { bridge, execution } = await fixture(context)
  execution.phase.validator = 'repair_classification'
  const valid = await bridge.validate(execution, { status: 'success', output: { category: 'dependency', root_cause: 'version mismatch', suggested_fix: 'pin package', repair_role: 'dependency_fixer' } })
  assert.equal(valid.status, 'success')
  const invalid = await bridge.validate(execution, { status: 'success', output: { category: '', root_cause: '', suggested_fix: '', repair_role: 'unknown' } })
  assert.equal(invalid.status, 'failure')
  assert.equal((invalid.output.validation_errors as string[]).length, 4)
})

test('operator failure evidence agrees with Python without diagnostic string heuristics', () => {
  const cases: Data[] = [
    {},
    { last_error: 'compile failed', evaluate_operator: { status: 'failed', errors: ['bad type'], summary: { passed: false } } },
    { script_command: 'build', script_exit_code: 1, script_duration: 0.5, script_stderr: 'compiler evidence\n'.repeat(400), candidate: { path: 'solution.json', sources: [{ value: 'x'.repeat(600), raw_response: 'excluded' }] } },
  ]
  const expected = JSON.parse(execFileSync(python, ['-c', `
import json, sys
sys.path.insert(0, sys.argv[1] + '/src')
from core.workflow_executor import WorkflowExecutor
json.dump([WorkflowExecutor._build_failure_evidence(case) for case in json.load(sys.stdin)], sys.stdout)
`, repository], { input: JSON.stringify(cases), encoding: 'utf8' }))
  assert.deepEqual(cases.map(failureEvidence), expected)
  assert.ok(failureEvidence({ script_stdout: 'unclassified raw evidence' }).includes('unclassified raw evidence'))
})
