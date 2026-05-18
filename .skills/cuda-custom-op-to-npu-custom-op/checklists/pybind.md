# PyTorch PyBind Adapter Experience Notes

This file captures optional reference notes for PyTorch pybind11 adapters between
`torch.Tensor` inputs and CANN custom ops. It is not a prescribed adapter path and
not a completion checklist.

## Adapter Generation Observations

- Generated ACLNN headers and migration mappings were more reliable than guessed
  signatures or copied template assumptions.
- Real adapter ELF or extension-module identity helped distinguish compiled
  adapters from source-only scaffolding, stubs, or placeholder modules.
- Exported symbols, pybind module attributes, Python import, callable resolution,
  and same-run coverage helped explain whether the host process used the intended
  adapter.
- Unique module names and build directories reduced accidental imports of stale
  `.so` files.

## PyTorch And ACLNN Observations

- `setup.py` was easier to reuse when CANN, custom OPP, and torch-npu paths came
  from placeholders or environment discovery rather than fixed local paths.
- torch-npu header and library paths were relevant when current-stream integration
  was used.
- Old extension artifacts sometimes hid rebuild results.
- The active ACLNN API shape and stride contract determined tensor descriptor
  construction.
- Non-contiguous tensor slices, non-zero `storage_offset()`, and non-compact strides
  affected descriptor and kernel indexing assumptions.
- Scalar attrs were easier to trust after generated-header inspection and runtime
  smoke evidence.
- Linking and loading the active vendor `op_api/lib` avoided stale `libcust_opapi.so`
  confusion.

## Direct Launch And Lifecycle Observations

- ACLRT direct launch debugging benefited from explicit notes on op type, shape,
  stream, tiling struct, binary path, blockDim, tensor layout, and mixed-op
  sequence.
- Cached binary handles avoided reuse crashes in some adapters.
- CANN registration conventions sometimes added suffixes to generated function
  names.
- Tiling struct size and raw-argument order were frequent sources of launch bugs.
- Mixed-op packages exposed descriptor leakage when executor state was shared across
  unrelated generated wrapper signatures.
- Embedded binary resource setups were clearer when resource registration was
  recorded before generated wrappers created executor space.

## Coverage Observations

- Import success alone described adapter availability, not custom-op execution.
- Same-run coverage tied to loaded adapter callable and accepted OPP producer gave
  stronger evidence than historical logs.
