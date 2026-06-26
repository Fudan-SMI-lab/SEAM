# Phase 1.5 - Migration Constraint Summary Generation (PPU)

Generate a migration constraint summary for a CUDA-to-PPU migration project.

## Project Directory
{project_dir}

## User-Provided Migration Constraints
The user has explicitly provided the following constraints for this migration:

{user_constraints}

## Goal
Produce a concise, actionable list of migration rules derived from the user constraints.

## Required Actions
1. Read each user constraint carefully and understand its intent.
2. For each user constraint, derive 1-2 specific, imperative migration rules. For example:
   - If user says "zero CPU fallback" → "Do not accept migrated execution paths that redirect PPU work to CPU fallback."
   - If user says "no modification of official source logic" → "Add new backend routing in backend_utils.py instead of modifying existing functions."
3. Keep the total list under 10 items.
4. Make each rule specific and testable — do NOT produce generic rules like "use PPU instead of CUDA".
5. **PPU-aware**: The target device API is `torch.cuda`. Do NOT produce rules that say "replace torch.cuda with torch.npu". PPU exposes CUDA-compatible APIs.

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
