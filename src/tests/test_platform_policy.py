"""Tests for platform_policy module: presets, parsing, inference, token helpers."""
import pytest
import textwrap
import tempfile
import os
from pathlib import Path

# Use the existing test infrastructure
PROJECT_ROOT = Path(__file__).resolve().parent.parent

from core.platform_policy import (
    PlatformPolicy,
    CustomOpEvidenceConfig,
    TargetPlatformConfig,
    parse_target_platform,
    resolve_policy,
    BUILTIN_PRESETS,
    _infer_policy_by_name,
    get_artifact_path_tokens,
    get_native_build_log_tokens,
    get_native_source_tokens,
    get_native_binary_tokens,
    get_target_device_values,
    get_positive_boolean_fields,
)
from core.config import load_workflow


class TestBuiltinPresets:
    """Verify all built-in presets exist and have required fields."""

    def test_npu_ascend_preset(self):
        p = BUILTIN_PRESETS["npu_ascend"]
        assert p.id == "npu_ascend"
        assert p.display_name == "Ascend NPU"
        assert "npu" in p.custom_op_evidence.target_device_values
        assert "npu_custom" in p.custom_op_evidence.positive_boolean_fields
        assert len(p.custom_op_evidence.native_build_log_tokens) > 0
        assert len(p.custom_op_evidence.native_binary_tokens) > 0
        assert len(p.custom_op_evidence.native_source_tokens) > 0
        assert p.custom_op_evidence.custom_op_evidence_policy != ""

    def test_ppu_cuda_compatible_preset(self):
        p = BUILTIN_PRESETS["ppu_cuda_compatible"]
        assert p.id == "ppu_cuda_compatible"
        assert p.display_name == "PPU (CUDA-Compatible)"
        assert "ppu" in p.custom_op_evidence.target_device_values
        assert "ppu_custom" in p.custom_op_evidence.positive_boolean_fields
        assert p.custom_op_evidence.custom_op_evidence_policy != ""

    def test_musa_muxi_preset_accepts_maca_tokens(self):
        p = BUILTIN_PRESETS["musa_muxi"]
        assert "musa" in p.custom_op_evidence.target_device_values
        assert "maca" in p.custom_op_evidence.target_device_values
        assert "maca" in p.custom_op_evidence.native_build_log_tokens
        assert "maca_custom_op_built" in p.custom_op_evidence.native_artifact_fields

    def test_all_presets_have_required_fields(self):
        for preset_id, policy in BUILTIN_PRESETS.items():
            assert policy.id == preset_id, f"{preset_id}: id mismatch"
            assert policy.display_name, f"{preset_id}: missing display_name"
            # generic_accelerator may have empty evidence configs
            if preset_id != "generic_accelerator":
                assert policy.custom_op_evidence.target_device_values, f"{preset_id}: missing target_device_values"
                assert policy.custom_op_evidence.positive_boolean_fields, f"{preset_id}: missing positive_boolean_fields"

    def test_all_expected_presets_exist(self):
        expected = {
            "npu_ascend",
            "ppu_cuda_compatible",
            "cuda_nvidia",
            "musa_muxi",
            "rocm_amd",
            "mlu_cambrian",
            "generic_accelerator",
        }
        assert set(BUILTIN_PRESETS.keys()) == expected


class TestInferPolicyByName:
    """Backward compatibility: infer policy from workflow name."""

    def test_npu_migration_infers_npu_ascend(self):
        p = _infer_policy_by_name("npu_migration_v2")
        assert p.id == "npu_ascend"

    def test_npu_migration_container_infers_npu_ascend(self):
        p = _infer_policy_by_name("npu_migration_v2_container")
        assert p.id == "npu_ascend"

    def test_ppu_migration_infers_ppu(self):
        p = _infer_policy_by_name("ppu_migration_v2_auto_vllm018_smoke")
        assert p.id == "ppu_cuda_compatible"

    def test_ppu_migration_container_infers_ppu(self):
        p = _infer_policy_by_name("ppu_migration_v2_container")
        assert p.id == "ppu_cuda_compatible"

    def test_unknown_name_infers_generic(self):
        p = _infer_policy_by_name("custom_workflow_v1")
        assert p.id == "generic_accelerator"

    def test_empty_name_infers_generic(self):
        p = _infer_policy_by_name("")
        assert p.id == "generic_accelerator"

    def test_case_insensitive(self):
        p = _infer_policy_by_name("NPU_MIGRATION_V2")
        assert p.id == "npu_ascend"


class TestResolvePolicy:
    """Top-level resolve_policy combining target_platform and inference."""

    def test_explicit_target_platform_wins(self):
        tp = TargetPlatformConfig(preset="ppu_cuda_compatible")
        p = resolve_policy(tp, "npu_migration_v2")
        assert p.id == "ppu_cuda_compatible"

    def test_none_target_platform_falls_back_to_inference(self):
        p = resolve_policy(None, "ppu_migration_v2")
        assert p.id == "ppu_cuda_compatible"

    def test_unknown_preset_raises(self):
        tp = TargetPlatformConfig(preset="unknown_xyz")
        with pytest.raises(ValueError, match="Unknown platform preset"):
            resolve_policy(tp, "test")


class TestParseTargetPlatform:
    """YAML target_platform parsing."""

    def test_none_returns_none(self):
        assert parse_target_platform(None) is None

    def test_valid_minimal(self):
        tp = parse_target_platform({"preset": "ppu_cuda_compatible"})
        assert tp is not None
        assert tp.preset == "ppu_cuda_compatible"
        assert tp.overrides == {}

    def test_valid_with_overrides(self):
        tp = parse_target_platform({
            "preset": "ppu_cuda_compatible",
            "overrides": {
                "id": "my_gpu",
                "custom_op_evidence": {
                    "target_device_values": ["my_gpu"],
                    "positive_boolean_fields": ["my_gpu_custom"],
                },
            },
        })
        assert tp is not None
        assert tp.preset == "ppu_cuda_compatible"
        assert tp.overrides["id"] == "my_gpu"

    def test_missing_preset_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            parse_target_platform({})

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            parse_target_platform("npu_ascend")

    def test_override_applied_to_policy(self):
        tp = TargetPlatformConfig(
            preset="ppu_cuda_compatible",
            overrides={
                "id": "custom_ppu",
                "display_name": "Custom PPU",
                "custom_op_evidence": {
                    "target_device_values": ["custom_dev"],
                    "positive_boolean_fields": ["custom_ok"],
                },
            },
        )
        p = resolve_policy(tp, "test")
        assert p.id == "custom_ppu"
        assert p.display_name == "Custom PPU"
        assert p.custom_op_evidence.target_device_values == ["custom_dev"]
        assert p.custom_op_evidence.positive_boolean_fields == ["custom_ok"]


class TestTokenHelpers:
    """Policy-aware token helpers with None fallback."""

    def test_get_target_device_values_none_fallback(self):
        vals = get_target_device_values(None)
        assert "npu" in vals
        assert "ascend" in vals

    def test_get_target_device_values_policy(self):
        vals = get_target_device_values(BUILTIN_PRESETS["ppu_cuda_compatible"])
        assert "ppu" in vals

    def test_get_positive_boolean_fields_none_fallback(self):
        fields = get_positive_boolean_fields(None)
        assert "npu_custom" in fields

    def test_get_positive_boolean_fields_policy(self):
        fields = get_positive_boolean_fields(BUILTIN_PRESETS["ppu_cuda_compatible"])
        assert "ppu_custom" in fields

    def test_get_artifact_path_tokens_fallback(self):
        tokens = get_artifact_path_tokens(None)
        assert "ascend" in tokens
        assert "cann" in tokens

    def test_get_artifact_path_tokens_generic_returns_own_tokens(self):
        """Explicit generic_accelerator returns its own tokens, NOT NPU fallback."""
        tokens = get_artifact_path_tokens(BUILTIN_PRESETS["generic_accelerator"])
        # Should contain generic tokens, NOT ascend/cann NPU tokens
        assert "compiled" in tokens or "custom_op" in tokens
        assert "ascend" not in tokens  # must NOT fall back to NPU
        assert "cann" not in tokens

    def test_get_build_log_tokens_fallback(self):
        tokens = get_native_build_log_tokens(None)
        assert "aclrt" in tokens


class TestConfigLoadWorkflow:
    """Integration: config.py load_workflow with target_platform."""

    def _write_yaml(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False, encoding="utf-8")
        f.write(textwrap.dedent(content))
        f.close()
        return f.name

    def test_no_target_platform_still_loads(self):
        yaml = """
        name: test_workflow
        version: "1.0"
        phases:
          - id: phase_1
            name: Phase 1
            prompt_template: test.md
            output_schema: {}
            transitions:
              on_success: complete
        terminals:
          complete: Done
        """
        path = self._write_yaml(yaml)
        try:
            wf = load_workflow(path)
            assert wf.target_platform is None  # No target_platform key
        finally:
            os.unlink(path)

    def test_target_platform_parsed(self):
        yaml = """
        name: ppu_migration_test
        version: "1.0"
        target_platform:
          preset: ppu_cuda_compatible
        phases:
          - id: phase_1
            name: Phase 1
            prompt_template: test.md
            output_schema: {}
            transitions:
              on_success: complete
        terminals:
          complete: Done
        """
        path = self._write_yaml(yaml)
        try:
            wf = load_workflow(path)
            assert wf.target_platform is not None
            assert wf.target_platform.preset == "ppu_cuda_compatible"
        finally:
            os.unlink(path)

    def test_target_platform_with_overrides(self):
        yaml = """
        name: custom_workflow
        version: "1.0"
        target_platform:
          preset: ppu_cuda_compatible
          overrides:
            id: my_accel
        phases:
          - id: phase_1
            name: Phase 1
            prompt_template: test.md
            output_schema: {}
            transitions:
              on_success: complete
        terminals:
          complete: Done
        """
        path = self._write_yaml(yaml)
        try:
            wf = load_workflow(path)
            assert wf.target_platform is not None
            assert wf.target_platform.overrides["id"] == "my_accel"
        finally:
            os.unlink(path)
