# Validation Script Examples

The Python files in this directory are example utilities from prior CUDA-to-Ascend
custom-op migrations. They are not automatic gates, task requirements, or completion
policies for this experience document.

They illustrate the kinds of checks that were useful in some projects:

- comparing kernel output with a CPU reference;
- checking adapter import and repeated-call behavior;
- comparing project baseline and custom-op paths;
- recording manifest artifacts and report parity.

Each project can adapt, ignore, or replace these scripts according to its own
scope and tooling.
