# Phase 1.5 - Migration Constraint Summary Generation

You have just completed Phase 1 project analysis for a CUDA-to-NPU migration project.

## Project Directory
{project_dir}

## Phase 1 Analysis Results
{phase_1_context}

## User-Provided Migration Constraints
The user has explicitly provided the following constraints for this migration:

{user_constraints}

## Goal
Produce a concise, actionable list of migration rules derived from the user constraints, adapted to the specific project context you analyzed in Phase 1.

## Required Actions
1. Read each user constraint carefully and understand its intent.
2. Cross-reference with your Phase 1 analysis (project structure, dependencies, CUDA patterns, compiled extensions).
3. For each user constraint, derive 1-2 specific, imperative migration rules that apply to THIS project. For example:
   - If user says "zero CPU fallback", and Phase 1 found a compiled CUDA/C++ extension used by Python → "Port every source-discovered custom-op unit exposed through the project API from CUDA/C++ to Ascend NPU, and do not redirect NPU execution to CPU fallback paths."
   - If user says "no modification of official source logic" → "Add new backend routing in backend_utils.py instead of modifying existing functions."
4. Keep the total list under 10 items.
5. Make each rule specific, testable, and project-aware — do NOT produce generic rules like "use NPU instead of CUDA".
6. If Phase 1 reports `custom_op_surface.custom_op_detected=true`, include a binding custom-op-only rule that every downstream manifest/source-inventory/final-gate row must carry real same-run route evidence through either `public_api_route_evidence` or `framework_integration_route_evidence`, correlated to that row. Do not add this rule for ordinary/non-custom CUDA projects.

## Hard Rules
- Do not dilute or remove user constraints. If a constraint is technically challenging, note the challenge but still include it as a rule.
- If a user constraint conflicts with the project's architecture, flag it and explain why, but still include it.
- The rules you generate WILL be injected into ALL subsequent phases (Phase 2, 3, 4, 5 repair agents, error analyzer, and review agent). They are binding.
- When the active custom-op rule above exists, direct-only, builtin-only, fallback, zero-call, report-only, synthetic/mock, benchmark-only, ATen-only, NpuExtension-only, CppExtension-only, Python-shim, or baseline-only route evidence must be rejected; ordinary CUDA projects remain unaffected.

## Output Format
End with a JSON block:
```json
{
  "constraint_summary": "1. [rule]\n2. [rule]\n3. [rule]...",
  "constraint_count": 3,
  "challenges_flagged": ["If any constraint has technical challenges, note them here"]
}
```
