from __future__ import annotations

from collections import Counter
from pathlib import Path
from collections.abc import Iterable
from collections.abc import Mapping
from typing import cast


from core.assisted_verification import validate_phase1_assisted_report
from core.custom_op_variants import normalize_phase1_project_analysis
from core.custom_op_variants import normalize_project_analysis_expanded_variants
from core.custom_op_variants import source_template_expanded_variants
from validators.validate_project_analysis import validate as validate_project_analysis


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def _write_deepwave_native_sources(root: Path) -> None:
    _write_storage_macro_sources(root)
    for family, symbols in {
        "scalar": ["forward", "backward"],
        "scalar_born": ["forward", "backward", "backward_sc"],
        "acoustic": ["forward", "backward"],
        "elastic": ["forward", "backward"],
    }.items():
        _ = (root / f"{family}.cu").write_text(
            """#define CAT_I(name, ndim, accuracy, dtype) name##_##ndim##d_##accuracy##_##dtype##_cuda
#define FUNC(name) CAT_I(name, DW_NDIM, DW_ACCURACY, DW_DTYPE)
// generated over ndim={1,2,3}, accuracy={2,4,6,8}, dtype={float,double}, device=cuda
extern "C" {
"""
            + "\n".join(f"int FUNC({symbol})(void) {{ return 0; }}" for symbol in symbols)
            + "\n}\n",
            encoding="utf-8",
        )
    _ = (root / "simple_compress.h").write_text(
        """// generated over ndim={1,2,3}, dtype={float,double}, device=cuda
int compress_cuda(void);
int decompress_cuda(void);
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
    _write_deepwave_native_sources(root)
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


def _variant_identity_with_axis_order(row: Mapping[str, object], axis_order: list[str]) -> str:
    base = str(row.get("base_unit_identity") or "")
    axis_values = cast(dict[str, str], row.get("axis_values", {}))
    ordered_axes = [axis for axis in axis_order if axis in axis_values]
    ordered_axes.extend(axis for axis in axis_values if axis not in ordered_axes)
    return ":".join([base, *(f"{axis}={axis_values[axis]}" for axis in ordered_axes)])


def _phase1_report_for_variants(surface: Mapping[str, object], variant_ids: list[str]) -> dict[str, object]:
    fine_units = cast(list[str], surface["fine_grained_operator_units"])
    return {
        "phase_id": "phase_1_project_analysis",
        "track": "custom_op_variant",
        "verdict": "complete",
        "evidence": ["source-discovered custom-op variant inventory checked"],
        "missing_units": [],
        "extra_units": [],
        "missing_variants": [],
        "extra_variants": [],
        "collapsed_or_representative_rows": [],
        "unresolved_source_groups": [],
        "phase1_inventory": {
            "fine_grained_operator_units": fine_units,
            "expanded_operator_instances_count": len(variant_ids),
            "expanded_unit_identities": variant_ids,
        },
        "source_evidence_inventory": {
            "fine_grained_operator_units": fine_units,
            "expanded_unit_identities": variant_ids,
        },
    }


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


def test_normalize_phase1_project_analysis_infers_deepwave_template_axes_without_llm_axes(tmp_path: Path) -> None:
    project_dir = tmp_path / "deepwave_like_project"
    surface = _build_deepwave_like_surface(project_dir)
    surface.pop("variant_axes_detected", None)
    surface.pop("variant_axes", None)
    surface.pop("expanded_operator_variants", None)
    surface.pop("expanded_operator_instances_count", None)
    output: dict[str, object] = {
        "project_dir": str(project_dir),
        "custom_op_surface": surface,
    }

    normalize_phase1_project_analysis(output, project_dir=str(project_dir))

    normalized_surface = cast(dict[str, object], output["custom_op_surface"])
    variants = _dict_rows(cast(list[object], normalized_surface["expanded_operator_variants"]))
    assert output["migration_route"] == "custom_op_with_variants"
    assert normalized_surface["variant_axes_detected"] is True
    assert normalized_surface["expanded_operator_instances_count"] == 240
    assert len(variants) == 240
    counts = Counter(str(row.get("base_unit_identity", "")) for row in variants)
    assert counts["scalar:forward_cuda"] == 24
    assert counts["simple_compress:compress_cuda"] == 6
    assert counts["storage:save_snapshot_gpu"] == 6


def test_normalize_phase1_project_analysis_infers_real_deepwave_240_inventory() -> None:
    project_dir = PROJECT_ROOT.parent / "cuda_projects" / "04_Deepwave"
    assert project_dir.is_dir()
    output: dict[str, object] = {}

    normalize_phase1_project_analysis(output, project_dir=str(project_dir))

    surface = cast(dict[str, object], output["custom_op_surface"])
    variants = _dict_rows(cast(list[object], surface["expanded_operator_variants"]))
    assert output["migration_route"] == "custom_op_with_variants"
    assert surface["expanded_operator_instances_count"] == 240
    assert len(variants) == 240
    axes = cast(dict[str, object], surface["variant_axes"])
    assert axes["ndim"] == ["1d", "2d", "3d"]
    assert axes["accuracy"] == ["2", "4", "6", "8"]
    assert axes["dtype"] == ["float", "double"]


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


def test_phase1_assisted_report_accepts_semantic_variant_axis_order(tmp_path: Path) -> None:
    project_dir = tmp_path / "deepwave_like_project"
    surface = _build_deepwave_like_surface(project_dir)
    variants = source_template_expanded_variants(surface, project_dir=str(project_dir))
    reordered_variants: list[dict[str, object]] = []
    for row in variants:
        variant = dict(row)
        axis_values = cast(dict[str, str], variant.get("axis_values", {}))
        if "accuracy" in axis_values and "dtype" in axis_values:
            variant["unit_identity"] = _variant_identity_with_axis_order(
                variant,
                ["ndim", "accuracy", "dtype", "device"],
            )
        reordered_variants.append(variant)
    surface["expanded_operator_variants"] = reordered_variants
    surface["expanded_operator_instances_count"] = len(reordered_variants)
    phase_output = {"project_dir": str(project_dir), "custom_op_surface": surface}
    variant_ids = [str(row["unit_identity"]) for row in reordered_variants]

    assert len(variant_ids) == 240
    assert validate_phase1_assisted_report(_phase1_report_for_variants(surface, variant_ids), phase_output) == []


def test_phase1_assisted_report_rejects_variant_axis_value_mismatch(tmp_path: Path) -> None:
    project_dir = tmp_path / "deepwave_like_project"
    surface = _build_deepwave_like_surface(project_dir)
    variants = source_template_expanded_variants(surface, project_dir=str(project_dir))
    bad_variants = [dict(row) for row in variants]
    first_with_dtype = next(row for row in bad_variants if "dtype" in cast(dict[str, str], row.get("axis_values", {})))
    bad_axis_values = dict(cast(dict[str, str], first_with_dtype["axis_values"]))
    bad_axis_values["dtype"] = "half"
    first_with_dtype["axis_values"] = bad_axis_values
    first_with_dtype["unit_identity"] = _variant_identity_with_axis_order(
        first_with_dtype,
        ["ndim", "accuracy", "dtype", "device"],
    )
    surface["expanded_operator_variants"] = bad_variants
    surface["expanded_operator_instances_count"] = len(bad_variants)
    phase_output = {"project_dir": str(project_dir), "custom_op_surface": surface}
    variant_ids = [str(row["unit_identity"]) for row in bad_variants]

    errors = validate_phase1_assisted_report(_phase1_report_for_variants(surface, variant_ids), phase_output)

    assert any("normalized Phase 1 output" in error for error in errors)


def test_normalize_project_analysis_drops_source_discovered_alias_units(tmp_path: Path) -> None:
    surface = _build_deepwave_like_surface(tmp_path / "deepwave_like_project")
    fine_units = cast(list[str], surface["fine_grained_operator_units"])
    fine_units.extend(["storage_snapshot:save_snapshot_gpu", "storage_snapshot:load_snapshot_gpu"])
    cast(list[str], surface.setdefault("operator_families", [])).append("storage_snapshot")
    evidence = cast(list[object], surface["fine_grained_operator_unit_evidence"])
    evidence.extend([
        {
            "unit_identity": "storage_snapshot:save_snapshot_gpu",
            "source_evidence": ["storage_utils.cu:13 duplicate alias for save_snapshot_gpu"],
            "candidate_framework_integration_routes": ["snapshot save path"],
        },
        {
            "unit_identity": "storage_snapshot:load_snapshot_gpu",
            "source_evidence": ["storage_utils.cu:65 duplicate alias for load_snapshot_gpu"],
            "candidate_framework_integration_routes": ["snapshot load path"],
        },
    ])
    output: dict[str, object] = {
        "project_dir": str(tmp_path / "deepwave_like_project"),
        "custom_op_surface": surface,
    }

    normalize_project_analysis_expanded_variants(output)

    normalized_surface = cast(dict[str, object], output["custom_op_surface"])
    units = cast(list[str], normalized_surface["fine_grained_operator_units"])
    variants = _dict_rows(cast(list[object], normalized_surface["expanded_operator_variants"]))
    assert "storage_snapshot:save_snapshot_gpu" not in units
    assert "storage_snapshot:load_snapshot_gpu" not in units
    assert normalized_surface["expanded_operator_instances_count"] == 240
    assert len(units) == 13
    assert len(variants) == 240


def test_normalize_project_analysis_expanded_variants_ignores_llm_alias_and_inventory_axes(tmp_path: Path) -> None:
    surface = _build_deepwave_like_surface(tmp_path / "deepwave_like_project")
    fine_units = cast(list[str], surface["fine_grained_operator_units"])
    variant_axes = cast(dict[str, object], surface["variant_axes"])
    variant_axes["ndim"] = ["1d", "2d", "3d", "1", "2", "3"]
    variant_axes["units"] = fine_units
    for item in cast(list[object], surface["expanded_operator_variants"]):
        if not isinstance(item, dict):
            continue
        base_unit = str(item.get("base_unit_identity", ""))
        if base_unit.startswith("storage:"):
            source_evidence = item.setdefault("source_evidence", [])
            assert isinstance(source_evidence, list)
            source_evidence.extend([
                "scalar.cu:655 calls STORAGE_FUNC(save_snapshot_gpu) from accuracy-specialized propagator kernels",
                "acoustic.cu:1141 calls STORAGE_FUNC(save_snapshot_gpu) from accuracy-specialized propagator kernels",
                "elastic.cu:2273 calls STORAGE_FUNC(save_snapshot_gpu) from accuracy-specialized propagator kernels",
            ])
    output: dict[str, object] = {
        "project_dir": str(tmp_path / "deepwave_like_project"),
        "custom_op_surface": surface,
    }

    normalize_project_analysis_expanded_variants(output)

    normalized_surface_obj = output["custom_op_surface"]
    assert isinstance(normalized_surface_obj, dict)
    normalized_surface = cast(dict[str, object], normalized_surface_obj)
    variants = _dict_rows(cast(list[object], normalized_surface["expanded_operator_variants"]))
    normalized_axes = cast(dict[str, object], normalized_surface["variant_axes"])
    assert normalized_surface["expanded_operator_instances_count"] == 240
    assert len(variants) == 240
    assert normalized_axes["ndim"] == ["1d", "2d", "3d"]
    assert "units" not in normalized_axes
    assert all(
        "units" not in cast(dict[str, object], row.get("axis_values", {}))
        for row in variants
    )


def test_normalize_project_analysis_supplements_underreported_source_units(tmp_path: Path) -> None:
    root = tmp_path / "deepwave_like_project"
    surface = _build_deepwave_like_surface(root)
    reported_unit = "scalar_born:backward_sc_cuda"
    surface["discovery_sources_checked"] = ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"]
    surface["searched_source_roots"] = [str(root)]
    surface["searched_source_paths"] = [path.as_posix() for path in sorted(root.iterdir()) if path.is_file()]
    surface["negative_evidence"] = ["No unresolved external-only custom-op units"]
    surface["dynamic_loading_checks"] = ["project-local native CUDA exports are loaded through Deepwave backend utilities"]
    surface["build_load_checks"] = ["project-local CUDA extension sources are present"]
    surface["unresolved_source_groups"] = []
    surface["out_of_scope_source_groups"] = []
    surface["fine_grained_operator_units"] = [reported_unit]
    surface["discovered_operator_names"] = [reported_unit.replace(":", "_")]
    surface["native_operator_symbols"] = ["backward_sc_cuda"]
    surface["kernel_launch_sites"] = ["scalar_born.cu:1215 FUNC(backward_sc)"]
    selected_evidence: list[dict[str, object]] = []
    for item in cast(list[object], surface["fine_grained_operator_unit_evidence"]):
        if isinstance(item, dict):
            item_map = cast(dict[str, object], item)
            if item_map.get("unit_identity") == reported_unit:
                selected_evidence.append(item_map)
    surface["fine_grained_operator_unit_evidence"] = selected_evidence
    for item in selected_evidence:
        item["candidate_framework_integration_routes"] = ["Deepwave backend utility dispatch"]
    selected_variants: list[dict[str, object]] = []
    for item in cast(list[object], surface["expanded_operator_variants"]):
        if isinstance(item, dict):
            item_map = cast(dict[str, object], item)
            if item_map.get("base_unit_identity") == reported_unit:
                selected_variants.append(item_map)
    surface["expanded_operator_variants"] = selected_variants
    surface["expanded_operator_instances_count"] = len(selected_variants)
    output: dict[str, object] = {
        "project_dir": str(root),
        "dependencies": ["torch", "torch_npu"],
        "cuda_detected": True,
        "entry_script": "test_data_and_scripts/run_full_fwi_original.py",
        "custom_op_surface": surface,
    }

    normalize_project_analysis_expanded_variants(output)

    normalized_surface = cast(dict[str, object], output["custom_op_surface"])
    assert normalized_surface["expanded_operator_instances_count"] == 240
    assert len(cast(list[object], normalized_surface["expanded_operator_variants"])) == 240
    assert validate_project_analysis(output)["passed"] is True


def test_normalize_project_analysis_collapses_deepwave_iso_family_aliases(tmp_path: Path) -> None:
    root = tmp_path / "deepwave_like_project"
    surface = _build_deepwave_like_surface(root)
    alias_map = {
        "scalar:": "scalar_iso:",
        "scalar_born:": "scalar_born_iso:",
        "acoustic:": "acoustic_iso:",
        "elastic:": "elastic_iso:",
    }
    aliased_units: list[str] = []
    for unit in cast(list[str], surface["fine_grained_operator_units"]):
        aliased = unit
        for source, target in alias_map.items():
            if unit.startswith(source):
                aliased = unit.replace(source, target, 1)
                break
        aliased_units.append(aliased)
    surface["fine_grained_operator_units"] = [*aliased_units, *cast(list[str], surface["fine_grained_operator_units"])]
    output: dict[str, object] = {
        "project_dir": str(root),
        "custom_op_surface": surface,
    }

    normalize_project_analysis_expanded_variants(output)

    normalized_surface = cast(dict[str, object], output["custom_op_surface"])
    units = cast(list[str], normalized_surface["fine_grained_operator_units"])
    assert all("_iso:" not in unit for unit in units)
    assert normalized_surface["expanded_operator_instances_count"] == 240
