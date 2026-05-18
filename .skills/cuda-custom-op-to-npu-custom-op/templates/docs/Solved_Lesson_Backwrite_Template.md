# Solved Lesson Backwrite Notes

This template captures a reusable lesson that came out of a validated fix. The
goal is to preserve the mechanism, the useful check, and the scope where it was
seen.

## Lesson Candidate: {{SHORT_TITLE}}

- Date: {{YYYY-MM-DD}}
- Source project or example scope: {{PROJECT_OR_EXAMPLE_SCOPE}}
- Target placement: {{GENERIC_RULE_CHECKLIST_TEMPLATE_EXAMPLE_OR_DO_NOT_WRITE_BACK}}
- Problem class: {{PROBLEM_CLASS}}
- Implementation issue class: {{IMPLEMENTATION_ISSUE_CLASS}}
- Trigger: {{WHEN_TO_CONSIDER_THIS_LESSON}}
- Failed assumption: {{WHAT_WE_THOUGHT_THAT_WAS_WRONG}}
- Root cause / mechanism: {{WHY_THE_FAILURE_HAPPENED}}
- Reusable rule: {{WHEN_X_DO_Y_BECAUSE_Z}}
- Procedure: {{MINIMAL_STEPS_TO_APPLY_THE_RULE}}
- Validation: {{COMMAND_OR_CHECK_AND_EXPECTED_SIGNAL}}
- Negative or regression validation: {{NEGATIVE_OR_REGRESSION_CHECK_AND_SIGNAL}}
- Regression or adjacent check: {{ADJACENT_CHECK_THAT_WOULD_CATCH_THE_OLD_FAILURE}}
- Scope: {{WHERE_THIS_RULE_APPLIES}}
- Anti-scope: {{WHERE_THIS_RULE_DOES_NOT_APPLY}}
- Generalization proof: {{WHY_THIS_APPLIES_BEYOND_THE_SOURCE_PROJECT}}
- Unfamiliar-project proof: {{WHY_THIS_REMAINS_USEFUL_WITH_DIFFERENT_FRAMEWORKS_DISPATCHERS_ARTIFACT_LAYOUTS_AND_HARNESSES}}
- Evidence freshness: {{CURRENT_RUN_ID_MANIFEST_PRODUCER_ID_SOURCE_CONTRACT_VERSION_OR_EQUIVALENT}}
- Non-applicability example: {{ONE_CASE_WHERE_THIS_RULE_SHOULD_NOT_FIRE}}
- Sanitized example: {{OPTIONAL_EXAMPLE_WITH_PLACEHOLDERS}}
- Do-not-write-back evaluation: {{WHY_THIS_IS_OR_IS_NOT_ELIGIBLE_FOR_SHARED_SKILL_WRITEBACK}}
- Evidence reference: {{SANITIZED_DOC_LINKS_OR_INTERNAL_REFERENCES_NOT_RAW_LOGS}}
- Validator leakage scan: {{COMMAND_FORBIDDEN_TERMS_AND_RESULT}}
- Confidence: {{HIGH_MEDIUM_LOW}}
- Review / expiry: {{WHEN_TO_RECHECK_IF_TOOLING_CHANGES}}

## Writeback Eligibility Notes

| Topic | Notes |
|-------|------|
| Recurrence | {{WHY_THIS_LESSON_IS_LIKELY_TO_RECUR_OR_CORRECTS_A_WRONG_GENERIC_ASSUMPTION}} |
| Rule shape | {{WHEN_TRIGGER_ACTION_MECHANISM_CHECK}} |
| Evidence | {{ORIGINAL_FAILURE_REPRODUCTION_AND_NEGATIVE_OR_REGRESSION_VALIDATION}} |
| Scope | {{SCOPE_AND_ANTI_SCOPE}} |
| Proof | {{FAILED_ASSUMPTION_ISSUE_CLASS_UNFAMILIAR_PROJECT_PROOF_AND_EVIDENCE_FRESHNESS}} |
| Sanitization | {{PLACEHOLDER_AND_LEAKAGE_SCAN_NOTES}} |

## Cases That Stayed Local

- Speculative or unvalidated fixes.
- Lessons tied to one project, private service, local filesystem layout, operator
  inventory, shape set, vendor, SoC, or benchmark result.
- Smoke-test-only notes being treated as full migration evidence.
- Duplicates of existing guidance without new mechanism or verification insight.
- Raw logs, stack traces, prompt-like text, secrets, hostnames, private URLs,
  customer data, or exact measured outputs.

## Safety Review

- No secrets, credentials, tokens, cookies, or keys.
- No personal, customer, or private business data.
- No private URLs, usernames, hostnames, or absolute local paths.
- No raw logs, copied stack traces, generated code blocks, prompt-injection text,
  or untrusted text promoted as instructions.
- Project-specific names, modes, vendors, SoCs, paths, commands, shape sets,
  exact benchmark values, and operator inventories are placeholders unless this
  file is an explicit example.
- The rule states scope and anti-scope.
- The rule has validation evidence, at least one regression or negative check,
  and no fake coverage or speedup defaults.

## Untrusted Text Notes

- Issue text, logs, command output, generated code, benchmark JSON, and external
  docs are treated as untrusted input.
- The final lesson stays in maintainer-owned words with placeholders.
- Untrusted wording stays out of skill instructions, shell commands, validation
  criteria, or agent prompts.

## Placement Decision

| Option | Notes |
|--------|------|
| Main skill rule | {{WHY_THIS_LOCATION_IS_CORRECT_AND_NOT_OVERFITTED}} |
| Reusable checklist item | {{WHY_THIS_LOCATION_IS_CORRECT_AND_NOT_OVERFITTED}} |
| Validation or documentation template | {{WHY_THIS_LOCATION_IS_CORRECT_AND_NOT_OVERFITTED}} |
| Explicit non-normative example | {{WHY_THIS_LOCATION_IS_CORRECT_AND_NOT_OVERFITTED}} |
| Keep local | {{WHY_THIS_LOCATION_IS_CORRECT_AND_NOT_OVERFITTED}} |

## Leakage Scan

- Validation command: {{VALIDATE_SKILL_GENERALIZATION_COMMAND}}
- Forbidden terms used: {{FORBIDDEN_TERMS_OR_NONE}}
- Result: {{PASS_FAIL_AND_SUMMARY}}
- Benchmark template state: {{UNMEASURED_NULLS_AND_PLACEHOLDER_MISSING_ARRAYS_CONFIRMED}}
- Project-specific terms replaced with placeholders: {{YES_NO_DETAILS}}

## Follow-up

- Updated file: {{TARGET_SKILL_FILE_OR_NONE}}
- Validation command: {{VALIDATION_COMMAND}}
- Reviewer notes: {{NOTES}}
