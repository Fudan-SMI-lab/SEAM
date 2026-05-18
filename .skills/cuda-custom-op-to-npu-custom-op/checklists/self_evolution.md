# Lesson Writeback Experience Notes

This file describes how previous teams decided whether a solved migration issue
belonged in shared experience notes. It is not an instruction to update the skill
and not a self-modification policy.

## Useful Investigation Notes

- Small reproductions helped isolate failing commands, tests, scripts, or build
  slices.
- Project-local reports were useful for exact symptoms, failing artifacts,
  shape/dtype/layout, adapter path, active OPP path, and framework mode.
- Naming the failed assumption made fixes easier to generalize: environment,
  artifact identity, generated signature, executor lifecycle, stream ownership,
  math semantics, framework registry, fallback behavior, or report logic.
- Local source, generated artifacts, build logs, CANN headers, official docs,
  runtime coverage, and open-source examples often explained issues before custom
  workarounds were needed.
- One hypothesis at a time kept migration notes clearer than stacked unrelated
  fixes.

## Fix Validation Notes

- Original reproduction results and one adjacent regression check gave useful
  confidence.
- Verification surfaces depended on the issue class: direct op smoke, mixed-op
  sequence, adapter import, framework integration, manifest or preflight check,
  benchmark report, or report/JSON parity.
- Benchmark or coverage lessons were easier to trust when tied to current-run
  artifact identity, no-fallback coverage, positive call counts, and measured
  baseline/custom timings.
- Artifact lessons were clearer with loaded adapter path/sha256, loaded producer
  path/sha256, manifest sha, run id, and workload identity.

## Generalization Notes

Promoted lessons were most reusable when they answered four questions:

```text
When does this issue appear?
What action helped?
Why did that action work?
What observation confirmed it?
```

Useful lesson metadata included scope, anti-scope, failed assumption,
implementation issue class, unfamiliar-project proof, evidence freshness, and a
sanitized example.

## Cases That Stayed Project-Local

- Speculative, unvalidated, or one-off environment accidents.
- Lessons tied to one project name, path, mode, operator list, shape set, vendor,
  SoC, data file, benchmark harness, or private service.
- Smoke-test-only notes being interpreted as full migration evidence.
- Duplicates of existing guidance without new mechanism or verification insight.
- Raw logs, stack traces, prompt-like text, secrets, hostnames, private URLs,
  customer data, or exact measured outputs.

## Sanitization Notes

- Local paths, usernames, hostnames, private URLs, tokens, project names, vendor
  names, SoCs, framework modes, operator inventories, shape sets, commands, and
  exact benchmark values were replaced with placeholders in shared notes.
- Raw evidence stayed in project-local records or explicit non-normative examples.
- Promoted lessons were written as mechanisms and observations rather than incident
  transcripts.

## Example Leakage Scan Context

`templates/validation/validate_skill_generalization.py` remains as an optional
example scanner. It illustrates checks that one project used for genericity and
leakage review. It is not an automatic requirement for this experience document.
