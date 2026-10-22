# KernelBench candidate packaging

In `npu_kernelbench_custom_op`, `solution.json` is a framework-generated artifact,
not a file maintained by the model. Generation and repair edit real source files
and an explicit `solution_spec.json` beside the configured solution path.

## Source contract

- `solution_spec.json` uses the evaluator's existing solution metadata schema.
  Its `sources` entries contain `path` and optional source metadata, but no
  embedded `content`.
- Source paths are relative to the directory containing the specification, not
  necessarily the task directory. Keep the entry point and build paths consistent
  with these paths; grouping sources under `sources/` is optional.
- Every evaluator input must be declared explicitly. The packer does not scan
  directories, infer file names, or rewrite source code based on diagnostics.
- Missing files, duplicate paths, paths outside the candidate root, symlinks,
  and references to reserved artifacts fail packaging. The old solution remains
  intact on failure, but is not silently evaluated as the new candidate.

## Loop handoff

1. Import an existing embedded solution once if there is no specification.
   Existing materialized source edits take precedence; only absent source files
   are restored from the legacy embedded content.
2. Package an existing specification before deciding whether initial generation
   is necessary. A source/embedded-content mismatch alone does not require the
   model to regenerate the operator.
3. After initial generation completes successfully, package its declared files
   and validate the resulting solution before evaluation.
4. After repair completes successfully, package the repaired files. The next
   evaluation therefore receives the repaired content without model writeback.
5. A failed generation/repair or packaging operation blocks publication and
   evaluation. Automatic packaging in a later iteration cannot bypass a failed
   publication; a successful producer must publish a valid candidate first.

For each evaluation, the framework copies the candidate into that evaluation's
own read-only `solution.json`. KernelBench consumes this snapshot. The evaluation
result and failure manifest record `evaluated_solution_path` and
`evaluated_solution_sha256` so later workspace edits cannot change which candidate
the recorded result describes.

Packaging and publication tests run without NPU hardware. They do not establish
that a generated Ascend C implementation compiles or is numerically correct.
