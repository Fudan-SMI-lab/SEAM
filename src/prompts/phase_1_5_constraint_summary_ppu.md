# Phase 1.5 - Migration Constraint Summary Generation (PPU)

You have just completed Phase 1 project analysis for a CUDA-to-PPU migration project.

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
2. Cross-reference with your Phase 1 analysis (project structure, dependencies, CUDA/PPU patterns, compiled extensions).
3. For each user constraint, derive 1-2 specific, imperative migration rules that apply to THIS project. For example:
   - If user says "zero CPU fallback", and Phase 1 found a compiled CUDA/C++ extension used by Python → "Port every source-discovered custom-op unit to PPU-compatible code, and do not redirect PPU execution to CPU fallback paths."
   - If user says "no modification of official source logic" → "Add new backend routing in backend_utils.py instead of modifying existing functions."
4. Keep the total list under 10 items.
5. Make each rule specific, testable, and project-aware — do NOT produce generic rules like "use PPU instead of CUDA".
6. **PPU-aware**: The target device API is `torch.cuda`. Do NOT produce rules that say "replace torch.cuda with torch.npu". PPU exposes CUDA-compatible APIs.

## Hard Rules
- Do not dilute or remove user constraints. If a constraint is technically challenging, note the challenge but still include it as a rule.
- If a user constraint conflicts with the project's architecture, flag it and explain why, but still include it.
- The rules you generate WILL be injected into ALL subsequent phases. They are binding.
- PPU rules must NOT require `torch_npu` installation, `torch.npu` API usage, or CANN/AscendC toolchains.

## Output Format
End with a JSON block:
```json
{
  "constraint_summary": "1. [rule]\n2. [rule]\n3. [rule]...",
  "constraint_count": 3,
  "challenges_flagged": ["If any constraint has technical challenges, note them here"]
}
```
