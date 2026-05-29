from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
from collections.abc import Iterable
from collections.abc import Mapping
from typing import cast


from core.assisted_verification import validate_phase1_assisted_report
from core.custom_op_variants import ensure_strict_expanded_variant_validation_script
from core.custom_op_variants import normalize_phase1_project_analysis
from core.custom_op_variants import normalize_project_analysis_expanded_variants
from core.custom_op_variants import source_template_expanded_variants
from core.platform_policy import BUILTIN_PRESETS
from validators.validate_validation_final import validate_custom_op_final_gate
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


def test_strict_expanded_variant_script_generation_is_deterministic_and_fail_closed(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    target: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "entry_script_path": "legacy_report_only.py",
        "run_command": "python legacy_report_only.py",
    }
    overlay = _expanded_variant_overlay(["op_alpha:float32", "op_alpha:float16"])

    ensure_strict_expanded_variant_validation_script(target, overlay, project_dir=str(project_dir))
    script_path = Path(cast(str, target["entry_script_path"]))
    first_text = script_path.read_text(encoding="utf-8")
    ensure_strict_expanded_variant_validation_script(target, overlay, project_dir=str(project_dir))
    second_text = script_path.read_text(encoding="utf-8")

    assert script_path == project_dir / "validate_custom_ops_full.py"
    assert target["run_command"] == f"python {script_path}"
    assert "SEAM_STRICT_EXPANDED_VARIANT_VALIDATOR_V1" in first_text
    assert first_text == second_text

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=project_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "required report missing" in result.stderr
    gate_data = cast(object, json.loads((project_dir / "migration_reports" / "custom_op_final_gate.json").read_text(encoding="utf-8")))
    assert isinstance(gate_data, dict)
    gate = cast(dict[str, object], gate_data)
    assert gate["inventory_count"] == 2
    assert gate["manifest_entries"] == 2
    assert gate["closed_pass_entries"] == 0
    assert gate["remaining_entries"] == 2
    assert gate["full_migration_status"] == "INCOMPLETE"
    assert gate["project_e2e_passed"] is False
    rows = cast(list[object], gate["rows"])
    assert len(rows) == 2
    assert {cast(dict[str, object], row)["unit_identity"] for row in rows} == {"op_alpha:float32", "op_alpha:float16"}
    assert _report_units(gate["source_inventory"]) == {"op_alpha:float32", "op_alpha:float16"}
    assert _report_units(gate["runtime_coverage_report"]) == {"op_alpha:float32", "op_alpha:float16"}
    assert _report_units(gate["performance_report"]) == {"op_alpha:float32", "op_alpha:float16"}
    validation = validate_custom_op_final_gate(gate, project_root=project_dir)
    assert not any("must be an integer" in error for error in validation["errors"])
    assert not any("rows must be a non-empty list" in error for error in validation["errors"])


def test_strict_expanded_variant_generated_script_builds_full_pass_gate_from_complete_reports(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    units = ["op_alpha:float32", "op_alpha:float16"]
    target: dict[str, object] = {"entry_script_kind": "custom_op_full_validation"}
    ensure_strict_expanded_variant_validation_script(target, _expanded_variant_overlay(units), project_dir=str(project_dir))
    _write_complete_generic_custom_op_reports(project_dir, units)

    script_path = Path(cast(str, target["entry_script_path"]))
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=project_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    gate_data = cast(object, json.loads((project_dir / "migration_reports" / "custom_op_final_gate.json").read_text(encoding="utf-8")))
    assert isinstance(gate_data, dict)
    gate = cast(dict[str, object], gate_data)
    assert gate["inventory_count"] == 2
    assert gate["manifest_entries"] == 2
    assert gate["closed_pass_entries"] == 2
    assert gate["remaining_entries"] == 0
    assert gate["full_migration_status"] == "FULL_PASS"
    assert gate["project_e2e_passed"] is True
    validation = validate_custom_op_final_gate(
        gate,
        project_root=project_dir,
        platform_policy=BUILTIN_PRESETS["generic_accelerator"],
    )
    assert validation == {"passed": True, "errors": [], "warnings": []}


def test_strict_expanded_variant_script_generation_preserves_sufficient_existing_script(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    script_path = project_dir / "validate_custom_ops_full.py"
    script_path.parent.mkdir(parents=True)
    sufficient_text = """
# SEAM_STRICT_EXPANDED_VARIANT_VALIDATOR_V1
# SEAM_STRICT_CUSTOM_OP_FINAL_GATE_SCAFFOLD_V1
# migration_reports migration_manifest.json runtime_coverage.json performance.json build.json
# implementation_resolution.json custom_op_final_gate.json evidence_validation.json
# expanded_variant_inventory variant_axis_coverage per_variant unit_identity source_inventory
# runtime_coverage_report performance_report required report missing per-expanded-variant
""".lstrip()
    _ = script_path.write_text(sufficient_text, encoding="utf-8")
    target: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "entry_script_path": "validate_custom_ops_full.py",
        "run_command": "python validate_custom_ops_full.py",
    }

    ensure_strict_expanded_variant_validation_script(target, _expanded_variant_overlay(["op_alpha:float32"]), project_dir=str(project_dir))

    assert script_path.read_text(encoding="utf-8") == sufficient_text
    assert target["entry_script_path"] == str(script_path)
    assert target["run_command"] == f"python {script_path}"


def test_strict_expanded_variant_script_generation_ignores_non_custom_op_or_missing_inventory(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    non_custom_target: dict[str, object] = {"entry_script_kind": "ordinary", "entry_script_path": "run.py"}
    ensure_strict_expanded_variant_validation_script(
        non_custom_target,
        _expanded_variant_overlay(["op_alpha:float32"]),
        project_dir=str(project_dir),
    )
    missing_inventory_target: dict[str, object] = {"entry_script_kind": "custom_op_full_validation"}
    ensure_strict_expanded_variant_validation_script(missing_inventory_target, {}, project_dir=str(project_dir))

    assert non_custom_target == {"entry_script_kind": "ordinary", "entry_script_path": "run.py"}
    assert missing_inventory_target == {"entry_script_kind": "custom_op_full_validation"}
    assert not (project_dir / "validate_custom_ops_full.py").exists()


def _expanded_variant_overlay(unit_identities: list[str]) -> dict[str, object]:
    return {
        "expanded_variant_inventory": {
            "variant_axes_detected": True,
            "unit_identities": unit_identities,
            "expanded_operator_instances_count": len(unit_identities),
        },
        "variant_axis_coverage": {"required": True, "axes": ["dtype"]},
        "per_variant_performance_report": {"required": True, "one_entry_per_expanded_variant": True},
    }


def _write_complete_generic_custom_op_reports(project_dir: Path, units: list[str]) -> None:
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_json(reports_dir / "migration_manifest.json", {"required_units": units})
    _write_json(reports_dir / "operator_inventory.json", {"entries": [_source_inventory_entry(project_dir, unit) for unit in units]})
    _write_json(reports_dir / "build.json", {"entries": [_build_entry(project_dir, unit) for unit in units]})
    _write_json(reports_dir / "implementation_resolution.json", {"entries": [_adapter_entry(unit) for unit in units]})
    _write_json(reports_dir / "evidence_validation.json", {"entries": [_route_and_parity_entry(unit) for unit in units]})
    _write_json(reports_dir / "runtime_coverage.json", {"entries": [_runtime_entry(unit) for unit in units]})
    _write_json(reports_dir / "performance.json", _performance_report(units))


def _source_inventory_entry(project_dir: Path, unit: str) -> dict[str, object]:
    source_path = _source_path_for_unit(unit)
    source_file = project_dir / source_path
    source_file.parent.mkdir(parents=True, exist_ok=True)
    _ = source_file.write_text("#include <stdint.h>\nvoid op_kernel_launch(void) {}\n", encoding="utf-8")
    return {
        "name": unit,
        "unit_identity": unit,
        "variant_or_signature": unit,
        "inventory_granularity": "fine_grained",
        "native_operator_symbols": [_symbol_for_unit(unit)],
        "kernel_functions": [f"{_symbol_for_unit(unit)}_kernel"],
        "kernel_launch_sites": [f"{source_path}:op_kernel_launch"],
        "public_entry_mapping": {"python_api": f"ops.{_symbol_for_unit(unit)}"},
        "source_evidence": [source_path],
        "source_path": source_path,
    }


def _build_entry(project_dir: Path, unit: str) -> dict[str, object]:
    symbol = _symbol_for_unit(unit)
    artifact_path = f"build/custom_op/lib/{symbol}.so"
    artifact_file = project_dir / artifact_path
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    _ = artifact_file.write_bytes(b"\x7fELF\x02\x01\x01\x00generic custom_op compiled native binary")
    build_log_path = f"migration_reports/{symbol}_build.log"
    _ = (project_dir / build_log_path).write_text("g++ -shared -fPIC src/op_kernel.cc -o " + artifact_path + "\n", encoding="utf-8")
    source_path = _source_path_for_unit(unit)
    return {
        "unit_identity": unit,
        "status": "PASS",
        "verified": True,
        "built": True,
        "loaded": True,
        "project_local": True,
        "custom_op_artifact": True,
        "path": artifact_path,
        "artifact_path": artifact_path,
        "runtime_loaded_artifact_path": artifact_path,
        "build_provenance": {"command": "g++ -shared -fPIC", "log_path": build_log_path},
        "source_evidence": [source_path],
        "native_operator_symbols": [symbol],
        "kernel_functions": [f"{symbol}_kernel"],
        "kernel_launch_sites": [f"{source_path}:op_kernel_launch"],
    }


def _adapter_entry(unit: str) -> dict[str, object]:
    return {"unit_identity": unit, "status": "PASS", "verified": True, "imported": True, "loaded": True, "linked": True}


def _route_and_parity_entry(unit: str) -> dict[str, object]:
    return {
        "unit_identity": unit,
        "status": "PASS",
        "verified": True,
        "passed": True,
        "parity_passed": True,
        "project_api_invoked": True,
        "custom_op_route_executed": True,
        "native_custom_op_route_executed": True,
        "compiled_kernel_executed": True,
        "max_abs_error": 0.0,
        "tolerance": 0.001,
    }


def _runtime_entry(unit: str) -> dict[str, object]:
    return {
        "unit_identity": unit,
        "status": "PASS",
        "verified": True,
        "same_run": True,
        "covered": True,
        "project_api_route": True,
        "project_api_invoked": True,
        "custom_op_route_executed": True,
        "native_custom_op_route_executed": True,
        "compiled_kernel_executed": True,
        "custom_call_count": 3,
        "fallback_detected": False,
        "zero_call_detected": False,
        "builtin_contamination_detected": False,
        "baseline_only_detected": False,
        "stub_detected": False,
    }


def _performance_report(units: list[str]) -> dict[str, object]:
    return {
        "complete": True,
        "unit_count": len(units),
        "path": "migration_reports/performance.json",
        "project_api_invoked": True,
        "custom_op_route_executed": True,
        "overall_project_api_invoked": True,
        "overall_all_units_replaced": True,
        "overall_baseline_seconds": 4.0,
        "overall_custom_seconds": 2.0,
        "overall_speedup_vs_baseline": 2.0,
        "baseline_device": "cuda",
        "custom_device": "accelerator",
        "overall_baseline_device": "cuda",
        "overall_custom_device": "accelerator",
        "custom": True,
        "entries": [_performance_entry(unit) for unit in units],
    }


def _performance_entry(unit: str) -> dict[str, object]:
    return {
        "unit_identity": unit,
        "status": "PASS",
        "verified": True,
        "project_api_invoked": True,
        "custom_op_route_executed": True,
        "baseline_device": "cuda",
        "custom_device": "accelerator",
        "custom": True,
        "baseline_seconds": 2.0,
        "custom_seconds": 1.0,
        "speedup_vs_baseline": 2.0,
    }


def _source_path_for_unit(unit: str) -> str:
    return f"src/{_symbol_for_unit(unit)}.cc"


def _symbol_for_unit(unit: str) -> str:
    return unit.replace(":", "_").replace("/", "_")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    _ = path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _report_units(report: object) -> set[str]:
    assert isinstance(report, Mapping)
    report_map = cast(Mapping[object, object], report)
    entries = report_map.get("entries")
    assert isinstance(entries, list)
    entry_items = cast(list[object], entries)
    return {
        cast(str, cast(Mapping[object, object], entry)["unit_identity"])
        for entry in entry_items
        if isinstance(entry, Mapping)
    }
