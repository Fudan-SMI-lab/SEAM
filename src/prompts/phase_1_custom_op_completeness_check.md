# Phase 1 Custom-Op Completeness Check

Use OpenCode tools to verify that Phase 1 discovered every in-scope custom operator and every source-required expanded variant.

Return only the structured JSON report requested by SEAM. If any source group, binding, wrapper, build/load path, public API route, or variant axis is unresolved, set `verdict` to `incomplete` or `unknown` and list the exact gaps.
