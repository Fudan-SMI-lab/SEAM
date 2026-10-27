# Phase 3: Ascend C Custom-Op Preparation

Generate the initial candidate for the current single KernelBench operator.
This phase runs when the candidate is missing or structurally invalid. Existing
candidates use the repair prompt with the latest KernelBench evidence.

This is a bounded implementation task, not a KernelBench or CANN source-code
survey. Read only the supplied task definition, workload sample, reference,
the relevant custom-op skill, and the minimum build example needed for the
target SoC. Do not scan the whole KernelBench repository, anti-hack
implementation, CANN header tree, or unrelated operators. Do not write
exploratory test projects. Produce the candidate artifacts first, then run at
most one narrow structural check.

## Context

- Operator API policy: `{operator_constraints}`

- Operator workspace: `{operator_task_dir}`
- Operator name: `{operator_name}`
- Definition: `{definition_path}`
- Workloads: `{workload_path}`
- Target SoC: `{target_soc}`
- Analysis output: `{previous_outputs}`
- Runtime detection report: `{runtime_report}`
- Runtime configuration result: `{runtime_configuration}`
- Candidate root (source paths are relative to this directory): `{solution_source_dir}`
- Editable source specification: `{solution_spec_path}`
- Framework-generated solution (do not edit): `{solution_path}`

## Read Documentation and Verify APIs Before Implementation

Read the compact documentation index and API evidence from Phase 2 first.
Use the `Source` paths in Explicit Runtime Skills to open the skill entry point,
then read only the relevant linked example or API section. Resolve relative
links from the skill directory and benchmark examples from the configured
runtime root; do not assume historical absolute paths still exist. Do not scan
the full CANN header tree. If a required source cannot be read, record its path
and the unresolved question instead of inventing its contents.

Before using a nontrivial Ascend C, host, or adapter API:

1. Verify the exact qualified symbol name and declaration in the active SDK or
   version-matched official documentation. Do not guess enum spellings, overloads,
   template parameters, argument order, units, or supported conversions.
2. Check parameter/return types, supported dtypes, target SoC, alignment and
   synchronization requirements for the chosen overload. Verify names such as
   queue positions and tensor accessors from declarations, not memory.
3. Record the source path and symbol/section with the verified signature and
   applicability in the operator manifest. Reuse Phase 2 evidence only when it
   matches the active toolchain and the API actually used.
4. Treat runtime API availability as evidence of existence, not authorization or
   semantic equivalence. Any fallback must independently satisfy the API policy,
   operator semantics and installed signature. Never automatically call a target
   operator implementation because a runtime report lists it as a fallback.
5. If the evidence is missing, state the uncertainty. If no compliant kernel
   implementation is feasible, return `prepared: false`, `build_status: "blocked"`
   with a factual blocking explanation; do not invent APIs or claim a build ran.

Do not implement error-message string detectors or mechanical source rewrites.
The fixed Python API allowlist is a separate policy check and remains binding.

## Semantics and Memory Safety

Use `definition.json` as the operator contract and the reference as the evaluator's
comparison baseline. A mismatch or a TODO alone does not prove a reference defect.
Record a suspected conflict with the exact contract, source branch and affected
workload; do not edit the reference, weaken comparison, or claim a pass.

For every workload dtype and shape family, reason about address bounds, GM/UB
allocation sizes, element-versus-byte units, tile and tail lengths, alignment,
queue ownership and synchronization. Verify the chosen copy/conversion APIs
against their actual declarations; do not prescribe scalar copies or a global
barrier as a universal fix. Include empty, smallest, largest and non-aligned
cases when they occur in the workload. A timeout or missing trace alone is not
proof of an out-of-bounds access.

## Required Actions

Create or update the real Ascend C candidate implementation in the operator workspace.

The operator API policy is binding. Do not introduce unlisted APIs or call an
existing implementation of the target operator through PyTorch, ATen, or
torch_npu. The candidate must dispatch the generated Ascend C custom-op.
Any `torch.*` or `torch_npu.*` API absent from the allowlist must not be used;
replace that approach with an allowlisted primitive or implement the operation
inside the Ascend C kernel. If no compliant approach is available, report a
blocked build instead of silently using the unlisted API.

Required artifacts:

- `solution_spec.json` beside the configured `solution.json`, containing the
  evaluator's solution metadata and an explicit `sources` list of objects with
  `path` but no `content`. Preserve the evaluator's metadata schema; do not invent
  replacement field names. Paths are relative to the directory containing the
  specification, and must point to real candidate files (prefer `sources/`).
- Ascend C host tiling/source;
- Ascend C device kernel/source;
- KernelBench adapter entry point;
- build configuration required by the chosen Ascend C builder;
- an operator manifest describing the generated implementation. The framework
  reads only the files explicitly listed in `solution_spec.json` and atomically
  creates `solution.json` after this phase. Do not create or edit `solution.json`,
  duplicate source text into the specification, or list build outputs/cache files.
  List all adapters, headers, device/host sources, and build inputs needed by the
  evaluator. To add or remove a source, update the explicit list.

Do not modify `definition.json` or `workload.jsonl`. Do not add CPU fallback, reference bypass, stub success, or report-only evidence. The implementation must be the candidate evaluated by KernelBench.

Do not claim the candidate passes evaluation. The enclosing workflow immediately
runs the authoritative full-workload KernelBench evaluation and API preflight.
Run only a narrow structural check if needed to ensure the candidate artifacts
are present and internally consistent; report actual results without replacing
the KernelBench result.

## Validation Ownership and Evidence

Inspect the complete workload metadata for dtype, shape and attribute coverage;
do not infer coverage or pass rates from a sample. Record the intended paths and
unverified risks in the manifest. This phase may run at most one narrow structural
check; do not launch an independent NPU evaluator or per-dtype test suite here.

The controller owns packaging and authoritative build/full-workload evaluation.
Report a command, exit status and log path for any check actually run. Use
`build_status: "not_run"` unless a real build was executed. Structural validity,
API inspection and candidate preparation do not establish compilation or runtime
correctness. Missing NPU access is not proof that the target runtime is broken;
leave runtime validation pending for the controller on the execution host.

## Output Format

Return exactly one JSON object:

```json
{
  "phase_id": "phase_3_custom_op_prepare",
  "operator": "{operator_name}",
  "operator_task_dir": "{operator_task_dir}",
  "solution_type": "ascendc",
  "solution_path": "{operator_task_dir}/solution.json",
  "source_dir": "{operator_task_dir}/sources",
  "manifest_path": "",
  "build_config_path": "",
  "prepared": true,
  "build_status": "not_run|success|failed|blocked",
  "blocking_category": "",
  "api_evidence": [],
  "validation_evidence": [],
  "modified_files": [],
  "summary": ""
}
```
