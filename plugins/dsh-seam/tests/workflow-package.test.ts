import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { stringify } from 'yaml'
import { buildWorkflowPackage, loadWorkflowPackage, verifyPackageBindings } from '../src/workflow-package.js'
import { generateWorkflow } from '../src/workflow-compiler.js'
import { executePhase } from '../src/phase-executor.js'
import type { Data, Execution, Workflow } from '../src/types.js'

const workflow: Workflow = { name: 'compiled', version: 1, phases: [{ id: 'validate', type: 'builtin', operation: 'operator_task_validate', input_mapping: { task: '${context.task}' } }] }

test('packages are deterministic, independent of YAML at runtime, and detect dependency drift and corruption', async context => {
  const root = await mkdtemp(join(tmpdir(), 'seam-package-'))
  context.after(() => rm(root, { recursive: true, force: true }))
  await mkdir(join(root, 'src/prompts'), { recursive: true })
  await writeFile(join(root, 'src/prompts/analyze.md'), 'original')
  const yaml = join(root, 'workflow.yaml')
  await writeFile(yaml, stringify(workflow))
  const bundle = await buildWorkflowPackage(yaml, join(root, 'packages'), root)
  const again = await buildWorkflowPackage(yaml, join(root, 'packages'), root)
  assert.equal(again.id, bundle.id)
  assert.equal(again.directory, bundle.directory)
  assert.ok(bundle.script.includes('case "validate":'))
  assert.ok(!bundle.script.includes('args.workflow'))
  assert.equal(bundle.sourceMap[0]?.yamlPath, 'phases[0]')
  assert.ok(bundle.sourceMap[0]!.endLine >= bundle.sourceMap[0]!.startLine)
  await rm(yaml)
  const loaded = await loadWorkflowPackage(bundle.directory, root)
  assert.deepEqual(loaded.stages.validate, workflow.phases[0])
  await writeFile(join(root, 'src/prompts/analyze.md'), 'changed')
  await assert.rejects(verifyPackageBindings(loaded, root), /dependencies changed/)
  await writeFile(join(bundle.directory, 'workflow.js'), 'return {status: "complete"}')
  await assert.rejects(loadWorkflowPackage(bundle.directory), /integrity mismatch/)
})

test('compiler rejects invalid schemas and emits stage-specific source ranges', async () => {
  await assert.rejects(generateWorkflow({ ...workflow, phases: [{ ...workflow.phases[0]!, input_schema: { type: 'invented' } }] }), /schema|type/)
  const compiled = await generateWorkflow(workflow)
  const entry = compiled.sourceMap[0]!
  const source = compiled.script.split('\n').slice(entry.startLine - 1, entry.endLine).join('\n')
  assert.match(source, /async function step_0/)
  assert.match(source, /runtime.leaf\("validate"/)
})

function execution(type = 'builtin'): Execution {
  return {
    runId: 'contract', workspace: '/task', signal: new AbortController().signal,
    phase: { id: 'contract', type, operation: 'operator_task_validate', input_schema: { type: 'object', required: ['count'], properties: { count: { type: 'integer' } }, additionalProperties: false }, output_schema: { type: 'object', required: ['passed'], properties: { passed: { type: 'boolean' } }, additionalProperties: false } },
    input: { count: 0 },
    scope: { context: {}, globals: {}, state: {}, loop_vars: {}, loop_state: {}, loop_history: [], step_outputs: {} },
  }
}

test('input contract blocks side effects; builtin output errors are not retried or published as success', async () => {
  let calls = 0
  const handler = async () => { calls++; return { status: 'success' as const, output: { passed: 'yes' } } }
  const invalid = execution()
  invalid.input = { count: '0' }
  const rejected = await executePhase(invalid, handler, undefined, async () => {}, 1)
  assert.equal(rejected.output.failure_kind, 'input_contract_failed')
  assert.equal(calls, 0)
  const result = await executePhase(execution(), handler, undefined, async () => {}, 1)
  assert.equal(result.status, 'failure')
  assert.equal(result.output.failure_kind, 'output_contract_failed')
  assert.equal(calls, 1)
})

test('LLM output contracts allow bounded same-phase correction and revalidate validator output', async () => {
  const request = execution('llm')
  let calls = 0
  const artifacts: string[] = []
  const result = await executePhase(request, async current => {
    calls++
    if (calls > 1) assert.ok(current.correction)
    return { status: 'success', output: { passed: calls > 1 ? true : 'yes' } }
  }, undefined, async name => { artifacts.push(name) }, 1)
  assert.equal(result.status, 'success')
  assert.equal(calls, 2)
  assert.deepEqual(artifacts, ['contract-parse-retry'])
  request.phase.validator = 'fixture'
  const invalidated = await executePhase(request, async () => ({ status: 'success', output: { passed: true } }), async () => ({ status: 'success', output: { passed: 1 } as Data }), async () => {}, 1)
  assert.equal(invalidated.status, 'failure')
})
