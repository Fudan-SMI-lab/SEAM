import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { parse } from 'yaml'
import { conditionDecision, reference, resolve } from '../src/expressions.js'
import type { Scope, Workflow } from '../src/types.js'

const repository = fileURLToPath(new URL('../../../', import.meta.url))
const scope: Scope = {
  state: { shared: 'state', phase: { ok: true } }, context: { shared: 'context', zero: 0 },
  globals: { shared: 'global', threshold: 3 }, loop_vars: { shared: 'variable' },
  loop_state: { shared: 'loop', count: 3, operator_passed: false, error_analysis: { repair_role: 'operator_fixer' }, evaluation_classification: { repair_role: 'operator_environment_fixer' } },
  step_outputs: { shared: 'step', phase: { ok: false }, items: [1, 2] }, loop_history: [{ iteration: 1 }],
}

function python(cases: { scope: Scope; expression: string; stop?: boolean; resolve?: boolean }[]) {
  return JSON.parse(execFileSync(process.env.SEAM_TEST_PYTHON ?? 'python3', ['-c', `
import json, sys
sys.path.insert(0, sys.argv[1] + '/src')
from core.variable_resolver import VariableResolver
from core.workflow_condition_policy import ConditionRequest, evaluate_condition
from core.workflow_stop_policy import StopCondition, select_stop_status
resolver = VariableResolver()
out = []
for case in json.load(sys.stdin):
    scope = case['scope']
    scopes = {**scope, 'globals': scope['globals']}
    if case.get('resolve'):
        out.append(resolver.resolve_dict(case['expression'], **scopes))
    elif case.get('stop'):
        decision = select_stop_status([StopCondition(case['expression'], 'hit')], scope['loop_state'], scope['globals'])
        out.append({'matched': decision.status == 'hit', 'error': bool(decision.evaluation_errors)})
    else:
        request = ConditionRequest(case['expression'], scope['state'], scope['globals'], scope['context'], scope['loop_vars'], scope['loop_state'], scope['step_outputs'])
        decision = evaluate_condition(request, lambda value: resolver.resolve(value, **scopes), lambda value: resolver._resolve_expr(value, **scopes))
        out.append({'matched': decision.matched, 'error': bool(decision.evaluation_error)})
json.dump(out, sys.stdout)
`, repository], { input: JSON.stringify(cases), encoding: 'utf8' })) as unknown[]
}

test('reference interpolation matches Python scope, type and default semantics', () => {
  const expressions = ['${shared}', '${phase.ok}', '${state.phase.ok}', '${context.zero}', '${missing}', '${missing | default false}', '${context.zero | default 9}', '${items.0}', '${loop_history}', '${state.*}', 'value=${missing}/${phase.ok}', '42', 'true']
  const cases = expressions.map(expression => ({ expression, scope, resolve: true }))
  assert.deepEqual(cases.map(entry => resolve(entry.expression, entry.scope)), python(cases))
  assert.equal(reference('phase.ok', scope), false)
})

test('phase and stop conditions match both Python evaluation modes', async () => {
  const workflow = parse(await readFile(new URL('../../../src/workflows/npu_kernelbench_custom_op.yaml', import.meta.url), 'utf8')) as Workflow
  const expressions = ['true', 'TRUE', 'false', '0', '1', '', '${phase.ok}', '${missing}', '${loop_state.count} >= 3', '$.count >= 3', 'shared == step', 'true or false and false', 'true or 1 < null', 'not false', "'x' in 'xyz'", '1 == true', 'null == false', '1 < 2 < 3', "'x' not in 'abc'", '(', '@', "'unterminated", 'true garbage', ...workflow.phases.flatMap(phase => phase.condition ? [phase.condition] : []), ...Object.values(workflow.sub_workflows ?? {}).flatMap(loop => [...loop.phases.flatMap(phase => phase.condition ? [phase.condition] : []), ...(loop.stop_conditions ?? []).map(stop => stop.condition)])]
  const cases = [false, true].flatMap(enhanced => expressions.flatMap(expression => [false, true].map(stop => ({ expression, scope: { ...scope, globals: { ...scope.globals, ...(enhanced ? { review_fail_closed: true } : {}) } }, stop }))))
  const expected = python(cases)
  cases.forEach((entry, index) => {
    const actual = conditionDecision(entry.expression, entry.scope, entry.stop)
    assert.deepEqual({ matched: actual.matched, error: !!actual.error }, expected[index], JSON.stringify({ expression: entry.expression, stop: entry.stop, globals: entry.scope.globals }))
  })
})
