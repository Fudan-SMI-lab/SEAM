from __future__ import annotations

from collections import Counter
from pathlib import Path
from collections.abc import Iterable
from collections.abc import Mapping
from typing import cast


from core.custom_op_variants import normalize_project_analysis_expanded_variants
from core.custom_op_variants import source_template_expanded_variants


PROPAGATOR_BASES = [
    "scalar:forward_cuda",
    "scalar:backward_cuda",
    "scalar_born:forward_cuda",
    "scalar_born:backward_cuda",
    "scalar_born:backward_sc_cuda",
    "acoustic:forward_cuda",
    "acoustic:backward_cuda",
    "elastic:forward_cuda",
    "elastic:backward_cuda",
]


def _write_storage_macro_sources(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _ = (root / "storage_utils.cu").write_text(
        """#include "storage_utils.h"
#if defined(DW_NDIM) && defined(DW_DTYPE)
extern "C" {
int STORAGE_FUNC(save_snapshot_gpu)(void* stream) { return 0; }
int STORAGE_FUNC(load_snapshot_gpu)(void* stream) { return 0; }
}
#endif
""",
        encoding="utf-8",
    )
    _ = (root / "storage_utils.h").write_text(
        """#if defined(DW_NDIM) && defined(DW_DTYPE)
#define SS_CAT_I(name, ndim, dtype) storage_##name##_##ndim##d_##dtype
#define SS_CAT(name, ndim, dtype) SS_CAT_I(name, ndim, dtype)
#define STORAGE_FUNC(name) SS_CAT(name, DW_NDIM, DW_DTYPE)
int STORAGE_FUNC(load_snapshot_gpu)(void* stream);
#endif
""",
        encoding="utf-8",
    )


def _native_symbols_for_units(units: list[str]) -> list[str]:
    symbols: list[str] = []
    for unit in units:
        name = unit.replace(":", "_")
        if name.startswith("storage_"):
            symbols.append(f"STORAGE_FUNC({name})")
        else:
            symbols.append(name)
    return symbols


def _evidence_for_unit(unit: str, source_evidence: list[str]) -> str:
    if unit in PROPAGATOR_BASES:
        return source_evidence[0]
    if unit.startswith("simple_compress"):
        return source_evidence[1]
    if unit == "storage:save_snapshot_gpu":
        return source_evidence[2]
    return source_evidence[3]


def _build_deepwave_like_surface(root: Path) -> dict[str, object]:
    _write_storage_macro_sources(root)
    fine_units = [
        *PROPAGATOR_BASES,
        "simple_compress:compress_cuda",
        "simple_compress:decompress_cuda",
        "storage:save_snapshot_gpu",
        "storage:load_snapshot_gpu",
    ]
    source_evidence = [
        "propagator exports: ndim={1,2,3}, accuracy={2,4,6,8}, dtype={float,double}, device=cuda",
        "simple_compress exports: ndim={1,2,3}, dtype={float,double}, device=cuda",
        "storage_utils.cu:11 STORAGE_FUNC(save_snapshot_gpu)",
        "storage_utils.cu:65 STORAGE_FUNC(load_snapshot_gpu)",
    ]
    return {
        "custom_op_detected": True,
        "discovery_complete": True,
        "variant_axes_detected": True,
        "variant_axes": {
            "ndim": ["1", "2", "3"],
            "accuracy": ["2", "4", "6", "8"],
            "dtype": ["float", "double"],
            "device": ["cuda", "gpu"],
        },
        "fine_grained_operator_units": fine_units,
        "discovered_operator_names": [unit.replace(":", "_") for unit in fine_units],
        "native_operator_symbols": _native_symbols_for_units(fine_units),
        "source_evidence": source_evidence,
        "fine_grained_operator_unit_evidence": [
            {
                "unit_identity": unit,
                "source_evidence": [_evidence_for_unit(unit, source_evidence)],
            }
            for unit in fine_units
        ],
        "expanded_operator_variants": [
            *[
                {
                    "unit_identity": f"{unit}:ndim=2:accuracy=4:dtype=float:device=cuda",
                    "base_unit_identity": unit,
                    "axis_values": {"ndim": "2", "accuracy": "4", "dtype": "float", "device": "cuda"},
                    "source_evidence": [f"{unit} generated over ndim, accuracy, dtype, device"],
                }
                for unit in PROPAGATOR_BASES
            ],
            *[
                {
                    "unit_identity": f"{unit}:ndim=2:dtype=float:device=cuda",
                    "base_unit_identity": unit,
                    "axis_values": {"ndim": "2", "dtype": "float", "device": "cuda"},
                    "source_evidence": [f"{unit} generated over ndim, dtype, device"],
                }
                for unit in ("simple_compress:compress_cuda", "simple_compress:decompress_cuda")
            ],
            {
                "unit_identity": "storage:save_snapshot_gpu:ndim=2:dtype=float:device=gpu",
                "base_unit_identity": "storage:save_snapshot_gpu",
                "axis_values": {"ndim": "2", "dtype": "float", "device": "gpu"},
                "source_evidence": ["storage_utils.cu:11 STORAGE_FUNC(save_snapshot_gpu)"],
            },
            {
                "unit_identity": "storage:load_snapshot_gpu:ndim=2:device=gpu",
                "base_unit_identity": "storage:load_snapshot_gpu",
                "axis_values": {"ndim": "2", "device": "gpu"},
                "source_evidence": ["storage_utils.cu:65 STORAGE_FUNC(load_snapshot_gpu)"],
            },
        ],
    }


def _dict_rows(items: Iterable[object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in items:
        if isinstance(item, Mapping):
            rows.append(cast(dict[str, object], item))
    return rows


def test_source_template_expanded_variants_reads_macro_preamble_for_storage_load(tmp_path: Path) -> None:
    surface = _build_deepwave_like_surface(tmp_path / "mini_project")
    rows = source_template_expanded_variants(surface, project_dir=str(tmp_path / "mini_project"))

    storage_rows = [row for row in rows if str(row.get("base_unit_identity", "")).startswith("storage:")]
    assert len(rows) == 240
    assert len(storage_rows) == 12
    assert Counter(str(row.get("base_unit_identity", "")) for row in storage_rows) == {
        "storage:save_snapshot_gpu": 6,
        "storage:load_snapshot_gpu": 6,
    }
    load_rows = [row for row in storage_rows if row.get("base_unit_identity") == "storage:load_snapshot_gpu"]
    assert all("dtype" in cast(dict[str, object], row.get("axis_values", {})) for row in load_rows)


def test_normalize_project_analysis_expanded_variants_recovers_deepwave_like_240_inventory(tmp_path: Path) -> None:
    surface = _build_deepwave_like_surface(tmp_path / "deepwave_like_project")
    output: dict[str, object] = {
        "project_dir": str(tmp_path / "deepwave_like_project"),
        "custom_op_surface": surface,
    }

    normalize_project_analysis_expanded_variants(output)

    normalized_surface_obj = output["custom_op_surface"]
    assert isinstance(normalized_surface_obj, dict)
    normalized_surface = cast(dict[str, object], normalized_surface_obj)
    variants_obj = normalized_surface["expanded_operator_variants"]
    assert isinstance(variants_obj, list)
    variants = _dict_rows(cast(list[object], variants_obj))
    assert normalized_surface["expanded_operator_instances_count"] == 240
    assert len(variants) == 240
    counts = Counter(str(row.get("base_unit_identity", "")) for row in variants)
    assert counts["storage:save_snapshot_gpu"] == 6
    assert counts["storage:load_snapshot_gpu"] == 6
    assert counts["simple_compress:compress_cuda"] == 6
    assert counts["simple_compress:decompress_cuda"] == 6
    assert all(
        "dtype" in cast(dict[str, object], row.get("axis_values", {}))
        for row in variants
        if row.get("base_unit_identity") == "storage:load_snapshot_gpu"
    )
