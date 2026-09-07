# V3 trace correlation schema

Task 23 adds observational correlation metadata to Task 21 raw OpenCode exports.
It does not make trace data an input to continuation resolution, hydration,
checkpoint selection, `RunOutcome`, or process exit mapping.

## Versioning

- `seam.opencode.raw-trace` remains schema version 1 when no typed correlation
  context is supplied to the standalone exporter.
- Active V3 correlated exports use `seam.opencode.raw-trace` schema version 2.
- Correlated per-session artifacts use `seam.opencode.raw-session` schema
  version 2. Their `raw_contract`, `messages`, session information, and unknown
  JSON values retain Task 21 semantics unchanged.
- The embedded `seam.trace-correlation` block is schema version 1.

## Manifest correlation block

`manifest.json.correlation` contains these separate record groups:

| Group | Stable linkage |
| --- | --- |
| `run_scope` | run, immediate parent run, lineage root, and optional parent trace identity |
| `phase_executions` | run + phase + deterministic phase execution ID |
| `phase5_attempts` | Task 18 accepted attempt ID and attempt number |
| `review_rounds` | Phase 5 iteration, Task 6 logical round, Task 1 `ReviewRound`, framework invocation, and session |
| `framework_invocations` | framework invocation, phase execution, and session |
| `transport_attempts` | Task 4 logical invocation, physical attempt ID/number, event phase, framework invocation, and session |
| `sessions` | Task 20 logical role/scope, root session, current session, and immediate parent session |
| `tool_calls` | root/current session, message, part, OpenCode `callID`, tool name, and optional Task child session |

All records carry one explicit `run_id`. Session and tool records are projected
while Task 21 visits the existing deterministic seed-order breadth-first graph;
the projector does not fetch or serialize a session a second time.

## Continuation parent reference

A child run may include `run_scope.parent_trace` with only:

```json
{
  "run_id": "parent-run-001",
  "manifest_path": "<absolute parent trace manifest identity>",
  "sha256": "<immutable SHA-256 from the Task 14 parent inventory>",
  "size_bytes": 1234
}
```

The reference is built from Task 14's already verified parent report inventory.
The child exporter does not read, copy, rewrite, or embed the parent manifest or
session payloads. `ContinuationPromptFacts` remains bounded and path-free and
never receives this reference or parent raw trace content.

## Completeness and authority

`correlation.complete` is false when correlation has an orphan, duplicate,
cross-run record, contradictory phase/review/attempt/parent relation, malformed
tool link, cycle, or required parent-trace gap. The `diagnostics` array records
those conditions independently from raw payloads and Task 21 source/export
errors. A correlation failure also makes the containing v2 trace manifest
incomplete; it never changes raw JSON values.

The v2 manifest explicitly sets `authority.correlation` to `false`. Trace and
correlation deletion or corruption therefore cannot select a parent, hydrate a
canonical value, change an anchor/checkpoint, rewrite `RunOutcome`, or alter the
normal exit mapping.

## Summary linkage

When a correlated export is available, `summary.json` adds:

```json
{
  "trace": {
    "correlation": {
      "schema_version": 1,
      "complete": true,
      "run_id": "child-run-001",
      "parent_run_id": "parent-run-001",
      "lineage_root_run_id": "parent-run-001",
      "diagnostics": []
    }
  }
}
```

The summary carries bounded linkage only. Raw OpenCode payloads remain under the
run-qualified trace directory and are never copied into the summary.
