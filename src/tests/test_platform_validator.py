"""Tests for validation_final.py with platform_policy support."""
import json
import pytest
import tempfile
import os
from pathlib import Path
from typing import cast

from core.platform_policy import BUILTIN_PRESETS, PlatformPolicy
from validators.validate_validation_final import (
    validate_custom_op_final_gate,
    _has_target_device_custom_proof,
    _path_has_platform_artifact_signal,
    _path_has_ascend_artifact_signal,
)


class TestValidateCustomOpFinalGate:
    """validate_custom_op_final_gate with and without platform_policy."""

    def test_legacy_npu_behavior_when_no_policy(self):
        """When platform_policy=None, NPU legacy behavior is preserved."""
        data: dict[str, object] = {
            "inventory_count": 1,
            "manifest_entries": 1,
            "closed_pass_entries": 1,
            "remaining_entries": 0,
            "full_migration_status": "FULL_PASS",
            "project_e2e_passed": True,
            "report_parity_passed": True,
            "rows": [],
        }
        result = validate_custom_op_final_gate(data, platform_policy=None)
        assert result["passed"] is False
        assert any("non-empty" in e for e in result.get("errors", []))

    def test_npu_policy_still_works(self):
        """NPU policy preserves current behavior."""
        npu = BUILTIN_PRESETS["npu_ascend"]
        data: dict[str, object] = {
            "inventory_count": 1,
            "manifest_entries": 1,
            "closed_pass_entries": 1,
            "remaining_entries": 0,
            "full_migration_status": "FULL_PASS",
            "project_e2e_passed": True,
            "report_parity_passed": True,
            "rows": [],
        }
        result = validate_custom_op_final_gate(data, platform_policy=npu)
        assert result["passed"] is False
        assert "passed" in result

    def test_ppu_full_pass_real_evidence(self):
        """PPU policy passes with truthful PPU/CUDA-compatible custom-op evidence.

        Creates a real project-root tree with:
        - migration_manifest.json (required_units)
        - custom_op_final_gate.json (PPU device evidence, ppu_custom: true)
        - Compiled .so binary with PPU tokens
        - Build log with PPU/CUDA tokens
        - Source file with CUDA __global__ tokens
        """
        ppu = BUILTIN_PRESETS["ppu_cuda_compatible"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "migration_reports"
            reports.mkdir()

            manifest = {"required_units": ["op_mm_ppu"]}
            (reports / "migration_manifest.json").write_text(json.dumps(manifest))

            build_dir = root / "ppu_custom" / "build"
            build_dir.mkdir(parents=True)
            build_log = build_dir / "build.log"
            build_log.write_text(
                "nvcc -shared -fPIC -o libppu_op.so kernel.cu -lcuda -lcudart\n"
                "ppu_compiler: compiled ppukernel successfully\n"
            )

            src_dir = root / "ppu_custom" / "src"
            src_dir.mkdir(parents=True)
            src_file = src_dir / "kernel.cu"
            src_file.write_text(
                '#include <cuda_runtime.h>\n'
                '#include <cuda.h>\n'
                '__global__ void ppu_kernel(float* out, const float* in, int n) {\n'
                '  int idx = blockIdx.x * blockDim.x + threadIdx.x;\n'
                '  if (idx < n) out[idx] = in[idx] * 2.0f;\n'
                '}\n'
            )

            so_path = build_dir / "libppu_op.so"
            so_data = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 128
            so_data += b"ppukernel\x00ppuccl\x00cuda\x00cudart\x00"
            so_path.write_bytes(so_data)

            relative_so = "ppu_custom/build/libppu_op.so"
            relative_src = "ppu_custom/src/kernel.cu"
            relative_log = "ppu_custom/build/build.log"

            gate: dict[str, object] = {
                "inventory_count": 1,
                "manifest_entries": 1,
                "closed_pass_entries": 1,
                "remaining_entries": 0,
                "full_migration_status": "FULL_PASS",
                "project_e2e_passed": True,
                "report_parity_passed": True,
                "source_inventory": {
                    "discovery_complete": True,
                    "discovery_sources_checked": [
                        "source", "bindings", "wrappers", "autograd",
                        "aliases", "launch", "setup", "tests",
                    ],
                    "out_of_scope_source_groups": [],
                    "op_mm_ppu": {
                        "name": "op_mm_ppu",
                        "native_operator_symbols": ["op_mm_ppu_impl"],
                        "kernel_functions": ["ppu_kernel"],
                        "source_evidence": {"native_source_paths": [relative_src]},
                        "unit_identity": "op_mm_ppu",
                        "variant_or_signature": "op_mm_ppu/v1",
                        "kernel_launch_sites": ["launch_op_mm_ppu"],
                        "public_entry_mapping": {"op_mm_ppu": "op_mm_ppu"},
                        "inventory_granularity": "FINE_GRAINED",
                    },
                },
                "performance_report": {
                    "complete": True,
                    "path": "migration_reports/performance.json",
                    "project_api_invoked": True,
                    "public_api_invoked": True,
                    "custom_op_route_executed": True,
                    "verified": True,
                    "unit_count": 1,
                    "baseline_device": "cuda",
                    "custom_device": "ppu",
                    "entries": {
                        "op_mm_ppu": {
                            "unit_identity": "op_mm_ppu",
                            "baseline_seconds": 0.5,
                            "custom_seconds": 0.1,
                            "speedup_vs_baseline": 5.0,
                            "project_api_invoked": True,
                            "public_api_invoked": True,
                            "custom_op_route_executed": True,
                            "baseline_device": "cuda",
                            "custom_device": "ppu",
                        }
                    },
                    "overall_baseline_seconds": 0.5,
                    "overall_custom_seconds": 0.1,
                    "overall_speedup_vs_baseline": 5.0,
                    "overall_project_api_invoked": True,
                    "overall_custom_op_route_executed": True,
                    "overall_all_units_replaced": True,
                },
                "rows": [
                    {
                        "name": "op_mm_ppu",
                        "status": "FULL_PASS",
                        "native_operator_symbols": ["op_mm_ppu_impl"],
                        "kernel_functions": ["ppu_kernel"],
                        "source_evidence": {"native_source_paths": [relative_src]},
                        "unit_identity": "op_mm_ppu",
                        "variant_or_signature": "op_mm_ppu/v1",
                        "kernel_launch_sites": ["launch_op_mm_ppu"],
                        "public_entry_mapping": {"op_mm_ppu": "op_mm_ppu"},
                        "inventory_granularity": "FINE_GRAINED",
                        "opp_custom_op_artifact_evidence": {
                            "project_local": True,
                            "in_project": True,
                            "built": True,
                            "present": True,
                            "loaded": True,
                            "verified": True,
                            "project_relative_path": relative_so,
                            "path": relative_so,
                            "runtime_loaded_module_file": relative_so,
                            "ppu_custom_op_artifact": True,
                            "ppu_custom_op_built": True,
                            "ppu_plugin_built": True,
                            "ppu_kernel_built": True,
                            "ppu_custom_op_loaded": True,
                            "build_provenance": {
                                "command": "nvcc -shared -o libppu_op.so kernel.cu -lcuda",
                                "log_path": relative_log,
                            },
                        },
                        "adapter_evidence": {"imported": True, "passed": True},
                        "parity_evidence": {"verified": True, "passed": True},
                        "integration_e2e_evidence": {
                            "project_api_invoked": True,
                            "public_api_invoked": True,
                            "custom_op_route_executed": True,
                            "native_custom_op_route_executed": True,
                            "compiled_kernel_executed": True,
                        },
                        "same_run_runtime_coverage": {
                            "same_run": True,
                            "project_api_route": True,
                            "public_api_route": True,
                            "custom_op_route_executed": True,
                            "native_custom_op_route_executed": True,
                            "compiled_kernel_executed": True,
                            "custom_call_count": 100,
                        },
                        "performance_evidence": {
                            "baseline_seconds": 0.5,
                            "custom_seconds": 0.1,
                            "speedup_vs_baseline": 5.0,
                            "project_api_invoked": True,
                            "public_api_invoked": True,
                            "custom_op_route_executed": True,
                            "baseline_device": "cuda",
                            "custom_device": "ppu",
                        },
                        "no_fallback_no_zero_call_no_builtin_contamination": {
                            "fallback_detected": False,
                            "zero_call_detected": False,
                            "builtin_contamination_detected": False,
                            "baseline_only_detected": False,
                            "stub_detected": False,
                        },
                    }
                ],
            }
            (reports / "custom_op_final_gate.json").write_text(json.dumps(gate))

            result = validate_custom_op_final_gate(gate, project_root=root, platform_policy=ppu)
            assert result["passed"], f"Expected PASS for PPU valid evidence, got errors: {result.get('errors')}"

    def test_musa_accepts_generic_library_path_with_platform_build_proof(self):
        musa = BUILTIN_PRESETS["musa_muxi"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "migration_reports"
            reports.mkdir()
            (reports / "migration_manifest.json").write_text(json.dumps({"required_units": ["op_generic_musa"]}))

            src_dir = root / "src" / "pkg"
            src_dir.mkdir(parents=True)
            source_path = src_dir / "kernel.cu"
            source_path.write_text("// maca_runtime mxgpu\n__global__ void kernel(float* x) {}\n")

            so_path = src_dir / "libcustom.so"
            so_path.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 256)
            build_log = reports / "build.log"
            build_log.write_text("mxcc -shared kernel.cu -o libcustom.so # MACA MetaX build\n")

            row: dict[str, object] = {
                "name": "op_generic_musa",
                "status": "FULL_PASS",
                "unit_identity": "op_generic_musa",
                "variant_or_signature": "op_generic_musa/v1",
                "native_operator_symbols": ["op_generic_musa_impl"],
                "kernel_functions": ["kernel"],
                "kernel_launch_sites": ["src/pkg/kernel.cu:2"],
                "public_entry_mapping": {"api": "op_generic_musa"},
                "source_evidence": {"native_source_paths": ["src/pkg/kernel.cu"]},
                "native_source_paths": ["src/pkg/kernel.cu"],
                "inventory_granularity": "FINE_GRAINED",
                "opp_custom_op_artifact_evidence": {
                    "project_local": True,
                    "built": True,
                    "loaded": True,
                    "project_relative_path": "src/pkg/libcustom.so",
                    "runtime_loaded_module_file": "/workspace/src/pkg/libcustom.so",
                    "build_provenance": {
                        "command": "mxcc -shared kernel.cu -o libcustom.so",
                        "log_path": "migration_reports/build.log",
                    },
                },
                "adapter_evidence": {"imported": True, "passed": True},
                "parity_evidence": {"verified": True, "passed": True},
                "integration_e2e_evidence": {
                    "project_api_invoked": True,
                    "custom_op_route_executed": True,
                    "native_custom_op_route_executed": True,
                },
                "same_run_runtime_coverage": {
                    "same_run": True,
                    "project_api_route": True,
                    "native_custom_op_route_executed": True,
                    "custom_call_count": 1,
                },
                "performance_evidence": {
                    "baseline_seconds": 1.0,
                    "custom_seconds": 0.5,
                    "speedup_vs_baseline": 2.0,
                    "project_api_invoked": True,
                    "baseline_device": "cuda",
                    "custom_device": "musa",
                },
                "no_fallback_no_zero_call_no_builtin_contamination": {
                    "fallback_detected": False,
                    "zero_call_detected": False,
                    "builtin_contamination_detected": False,
                    "baseline_only_detected": False,
                    "stub_detected": False,
                },
            }
            gate = {
                "inventory_count": 1,
                "manifest_entries": 1,
                "closed_pass_entries": 1,
                "remaining_entries": 0,
                "full_migration_status": "FULL_PASS",
                "project_e2e_passed": True,
                "report_parity_passed": True,
                "source_inventory": {
                    "discovery_complete": True,
                    "discovery_sources_checked": [
                        "source", "bindings", "wrappers", "autograd",
                        "aliases", "launch", "setup", "tests",
                    ],
                    "out_of_scope_source_groups": [],
                    "op_generic_musa": row,
                },
                "performance_report": {
                    "complete": True,
                    "path": "migration_reports/performance.json",
                    "verified": True,
                    "unit_count": 1,
                    "entries": {"op_generic_musa": {**cast(dict[str, object], row["performance_evidence"]), "unit_identity": "op_generic_musa"}},
                    "overall_baseline_seconds": 1.0,
                    "overall_custom_seconds": 0.5,
                    "overall_speedup_vs_baseline": 2.0,
                    "overall_project_api_invoked": True,
                    "overall_all_units_replaced": True,
                    "baseline_device": "cuda",
                    "custom_device": "musa",
                },
                "rows": [row],
            }

            result = validate_custom_op_final_gate(gate, project_root=root, platform_policy=musa)
            assert result["passed"], f"Expected PASS for generic .so path with MUSA proof, got: {result.get('errors')}"

    def test_generic_accelerator_does_not_accept_npu_fallback(self):
        """Explicit generic_accelerator must NOT pass merely from NPU legacy fallback.

        Creates a gate payload that would pass for NPU but should not for generic.
        """
        generic = BUILTIN_PRESETS["generic_accelerator"]
        data = {
            "inventory_count": 1,
            "manifest_entries": 1,
            "closed_pass_entries": 1,
            "remaining_entries": 0,
            "full_migration_status": "FULL_PASS",
            "project_e2e_passed": True,
            "report_parity_passed": True,
            "rows": [],
        }
        result = validate_custom_op_final_gate(data, platform_policy=generic)
        assert result["passed"] is False
        # Should fail because rows is empty, not because of NPU fallback issues
        assert any("non-empty" in e for e in result.get("errors", []))


class TestHasTargetDeviceCustomProof:
    """_has_target_device_custom_proof with policy-aware device checks."""

    def test_npu_policy_accepts_npu_device_value(self):
        assert _has_target_device_custom_proof(
            {"custom_device": "npu"}, BUILTIN_PRESETS["npu_ascend"]
        )

    def test_npu_policy_accepts_npu_custom_boolean(self):
        assert _has_target_device_custom_proof(
            {"npu_custom": True}, BUILTIN_PRESETS["npu_ascend"]
        )

    def test_npu_policy_rejects_ppu_device_value(self):
        assert not _has_target_device_custom_proof(
            {"custom_device": "ppu"}, BUILTIN_PRESETS["npu_ascend"]
        )

    def test_ppu_policy_accepts_ppu_device_value(self):
        assert _has_target_device_custom_proof(
            {"custom_device": "ppu"}, BUILTIN_PRESETS["ppu_cuda_compatible"]
        )

    def test_ppu_policy_accepts_ppu_custom_boolean(self):
        assert _has_target_device_custom_proof(
            {"ppu_custom": True}, BUILTIN_PRESETS["ppu_cuda_compatible"]
        )

    def test_ppu_policy_does_not_require_npu_custom(self):
        """PPU truthful evidence (ppu_custom=True) should pass WITHOUT npu_custom."""
        assert _has_target_device_custom_proof(
            {"ppu_custom": True}, BUILTIN_PRESETS["ppu_cuda_compatible"]
        )

    def test_ppu_policy_rejects_nonexistent_device(self):
        assert not _has_target_device_custom_proof(
            {"custom_device": "unknown"}, BUILTIN_PRESETS["ppu_cuda_compatible"]
        )

    def test_none_policy_falls_back_to_npu(self):
        """When policy is None, NPU legacy device values are used."""
        assert _has_target_device_custom_proof({"custom_device": "npu"}, None)
        assert not _has_target_device_custom_proof({"custom_device": "ppu"}, None)

    def test_none_policy_falls_back_to_npu_boolean(self):
        assert _has_target_device_custom_proof({"npu_custom": True}, None)

    def test_generic_policy_rejects_npu_device(self):
        """Explicit generic_accelerator must NOT accept npu via legacy fallback."""
        assert not _has_target_device_custom_proof(
            {"custom_device": "npu"}, BUILTIN_PRESETS["generic_accelerator"]
        )

    def test_generic_policy_accepts_generic_device(self):
        """Explicit generic_accelerator should accept its own device values."""
        assert _has_target_device_custom_proof(
            {"custom_device": "cuda"}, BUILTIN_PRESETS["generic_accelerator"]
        )
        assert _has_target_device_custom_proof(
            {"custom_device": "accelerator"}, BUILTIN_PRESETS["generic_accelerator"]
        )

    def test_none_policy_still_accepts_npu(self):
        """platform_policy=None must still accept NPU for backward compat."""
        assert _has_target_device_custom_proof({"custom_device": "npu"}, None)
        assert _has_target_device_custom_proof({"npu_custom": True}, None)


class TestPathHasPlatformArtifactSignal:
    """Path-based artifact signal detection with platform tokens."""

    def test_ascend_signal_legacy(self):
        assert _path_has_ascend_artifact_signal("opp/custom_op/build/libcustom.so")

    def test_platform_signal_npu(self):
        assert _path_has_platform_artifact_signal(
            "opp/custom_op/build/libcustom.so", BUILTIN_PRESETS["npu_ascend"]
        )

    def test_platform_signal_ppu(self):
        assert _path_has_platform_artifact_signal(
            "ppu_custom/build/libppu_op.so", BUILTIN_PRESETS["ppu_cuda_compatible"]
        )

    def test_platform_signal_no_match_ppu_for_npu(self):
        assert not _path_has_platform_artifact_signal(
            "ppu_custom/build/libppu_op.so", BUILTIN_PRESETS["npu_ascend"]
        )

    def test_platform_signal_none_fallback(self):
        # None falls back to legacy NPU tokens
        assert _path_has_platform_artifact_signal(
            "opp/custom_op/build/libcustom.so", None
        )

    def test_platform_signal_none_fallback_rejects_ppu(self):
        assert not _path_has_platform_artifact_signal(
            "ppu_custom/build/libppu_op.so", None
        )


class TestRepairLoopGuidance:
    """Test repair_loop guidance is platform-aware."""

    def test_generic_guidance_npu(self):
        from core.repair_loop import _operator_generic_guidance
        npu = BUILTIN_PRESETS["npu_ascend"]
        text = _operator_generic_guidance(
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=npu,
        )
        assert "Ascend NPU" in text
        assert "torch_npu" in text

    def test_generic_guidance_ppu(self):
        from core.repair_loop import _operator_generic_guidance
        ppu = BUILTIN_PRESETS["ppu_cuda_compatible"]
        text = _operator_generic_guidance(
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=ppu,
        )
        assert "PPU" in text
        assert "Ascend NPU" not in text

    def test_generic_guidance_none_defaults_to_npu(self):
        from core.repair_loop import _operator_generic_guidance
        text = _operator_generic_guidance(
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=None,
        )
        assert "Ascend NPU" in text

    def test_custom_op_guidance_npu_keep_ascend_tokens(self):
        from core.repair_loop import _operator_custom_op_guidance
        npu = BUILTIN_PRESETS["npu_ascend"]
        text = _operator_custom_op_guidance(
            "/tmp/ctx.json",
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=npu,
        )
        assert "Ascend OPP/CANN" in text
        assert "ACL/CANN/AscendC/OPP" in text

    def test_custom_op_guidance_ppu_avoids_ascend_tokens(self):
        from core.repair_loop import _operator_custom_op_guidance
        ppu = BUILTIN_PRESETS["ppu_cuda_compatible"]
        text = _operator_custom_op_guidance(
            "/tmp/ctx.json",
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=ppu,
        )
        assert "Ascend OPP/CANN" not in text
        assert "ACL/CANN/AscendC/OPP" not in text
        assert "PPU" in text

    def test_custom_op_guidance_none_defaults_to_npu(self):
        from core.repair_loop import _operator_custom_op_guidance
        text = _operator_custom_op_guidance(
            "/tmp/ctx.json",
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=None,
        )
        assert "Ascend OPP/CANN" in text


class TestCaseInsensitiveTokenMatching:
    """Proof that text and binary token matching is case-insensitive."""

    def test_text_token_case_insensitive(self):
        from validators.validate_validation_final import _text_has_any_token
        # Uppercase token should match lowercased text
        assert _text_has_any_token("-fPIC", ("-fPIC",))
        assert _text_has_any_token("-fpic", ("-fPIC",))
        # Mixed case text should match lowercased token
        assert _text_has_any_token("NvCC --compile", ("nvcc",))
        # Generic accelerator build log tokens
        assert _text_has_any_token("g++ -fPIC -shared", ("-fPIC",))
        assert _text_has_any_token("G++ -FPIC -SHARED", ("-fPIC",))

    def test_text_token_no_false_positive(self):
        from validators.validate_validation_final import _text_has_any_token
        assert not _text_has_any_token("nothing here", ("-fPIC", "nvcc"))
        assert not _text_has_any_token("", ("-fPIC",))

    def test_binary_token_case_insensitive_elf(self):
        import tempfile
        from validators.validate_validation_final import _binary_has_platform_native_token
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as f:
            # Write ELF magic + lowercase elf string
            f.write(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 128 + b"elf in binary")
            f.flush()
            path = f.name
        try:
            # Token b"ELF" should match the lowercased window b"elf..."
            assert _binary_has_platform_native_token(Path(path), (b"ELF",))
        finally:
            import os
            os.unlink(path)

    def test_binary_token_case_insensitive_cuda(self):
        import tempfile
        from validators.validate_validation_final import _binary_has_platform_native_token
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as f:
            f.write(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 128 + b"cuda runtime\x00cudart")
            f.flush()
            path = f.name
        try:
            assert _binary_has_platform_native_token(Path(path), (b"CUDA",))
            assert _binary_has_platform_native_token(Path(path), (b"cudart", b"nvcc"))
        finally:
            import os
            os.unlink(path)

    def test_binary_token_case_insensitive_uppercase_token_lowercase_data(self):
        import tempfile
        from validators.validate_validation_final import _binary_has_platform_native_token
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as f:
            f.write(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 128 + b"rocm\0amd\0hip")
            f.flush()
            path = f.name
        try:
            assert _binary_has_platform_native_token(Path(path), (b"ROCm", b"AMD"))
        finally:
            import os
            os.unlink(path)

    def test_binary_token_no_false_positive(self):
        import tempfile
        from validators.validate_validation_final import _binary_has_platform_native_token
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as f:
            f.write(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 256)
            f.flush()
            path = f.name
        try:
            assert not _binary_has_platform_native_token(Path(path), (b"cuda", b"nvcc"))
        finally:
            import os
            os.unlink(path)


class TestRepairLoopEnginePlatformPolicy:
    """Verify RepairLoopEngine threads platform_policy into guidance and gate."""

    @staticmethod
    def _mock_engine(policy=None):
        from unittest.mock import MagicMock
        from core.repair_loop import RepairLoopEngine
        artifact_store = MagicMock()
        artifact_store.artifact_dir = tempfile.mkdtemp()
        return RepairLoopEngine(
            session_mgr=MagicMock(),
            artifact_store=artifact_store,
            prompt_loader=MagicMock(),
            validator=MagicMock(),
            platform_policy=policy,
        )

    def test_ppu_policy_guidance_avoids_ascend(self):
        """RepairLoopEngine with PPU policy produces PPU guidance, not Ascend."""
        from core.repair_loop import _operator_custom_op_guidance, _operator_generic_guidance
        ppu = BUILTIN_PRESETS["ppu_cuda_compatible"]
        engine = self._mock_engine(policy=ppu)

        ctx = engine._build_repair_prompt(
            entry_script="run.py",
            project_dir="/tmp/test",
            iteration=1,
            error_text="ModuleNotFoundError: No module named 'custom_op'",
            classification={
                "category": "operator",
                "root_cause": "missing op",
                "suggested_fix": "add op",
                "repair_role": "operator_fixer",
            },
            history=[],
            constraint_summary="",
            env_context={},
            phase3_contract={
                "entry_script_kind": "custom_op_full_validation",
                "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
            },
            cmd_argv=["python", "run.py"],
            use_shell=False,
            script_cwd="/tmp/test",
            env_vars={},
        )
        # Verify the prompt was loaded (guidance was set as context)
        engine.prompt_loader.load_prompt.assert_called_once()

    def test_ppu_policy_produces_ppu_not_ascend_in_guidance(self):
        """Directly verify PPU engineer guidance excludes Ascend tokens."""
        from core.repair_loop import _operator_custom_op_guidance
        ppu = BUILTIN_PRESETS["ppu_cuda_compatible"]
        text = _operator_custom_op_guidance(
            "/tmp/ctx.json",
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=ppu,
        )
        assert "PPU" in text
        assert "Ascend OPP/CANN" not in text
        assert "ACL/CANN/AscendC/OPP" not in text

    def test_npu_policy_guidance_includes_ascend(self):
        """RepairLoopEngine with NPU policy produces Ascend guidance."""
        from core.repair_loop import _operator_custom_op_guidance
        npu = BUILTIN_PRESETS["npu_ascend"]
        text = _operator_custom_op_guidance(
            "/tmp/ctx.json",
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=npu,
        )
        assert "Ascend OPP/CANN" in text
        assert "ACL/CANN/AscendC/OPP" in text

    def test_no_policy_defaults_to_npu_guidance(self):
        """RepairLoopEngine without platform_policy defaults to NPU guidance."""
        from core.repair_loop import _operator_custom_op_guidance
        text = _operator_custom_op_guidance(
            "/tmp/ctx.json",
            project_dir="/tmp/test",
            entry_script="run.py",
            platform_policy=None,
        )
        assert "Ascend OPP/CANN" in text

    def test_validate_gate_passes_with_ppu_policy(self):
        """_validate_custom_op_final_gate_for_contract with PPU policy passes PPU evidence."""
        import tempfile, json, os
        from pathlib import Path
        from core.repair_loop import RepairLoopEngine
        ppu = BUILTIN_PRESETS["ppu_cuda_compatible"]
        engine = self._mock_engine(policy=ppu)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "migration_reports"
            reports.mkdir()

            # Minimal valid PPU gate payload
            manifest = {"required_units": ["op_ppu"]}
            (reports / "migration_manifest.json").write_text(json.dumps(manifest))

            # Create PPU artifact files
            build_dir = root / "ppu_custom" / "build"
            build_dir.mkdir(parents=True)
            (build_dir / "build.log").write_text("nvcc -shared -o libppu.so kernel.cu -lcuda\nppu_compiler: ok\n")
            so_path = build_dir / "libppu.so"
            so_path.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 128 + b"ppukernel\x00cuda\x00")
            src_dir = root / "ppu_custom" / "src"
            src_dir.mkdir(parents=True)
            (src_dir / "kernel.cu").write_text('#include <cuda_runtime.h>\n__global__ void ppu_kernel() {}\n')

            gate = {
                "inventory_count": 1, "manifest_entries": 1, "closed_pass_entries": 1,
                "remaining_entries": 0, "full_migration_status": "FULL_PASS",
                "project_e2e_passed": True, "report_parity_passed": True,
                "source_inventory": {
                    "discovery_complete": True,
                    "discovery_sources_checked": ["source","bindings","wrappers","autograd","aliases","launch","setup","tests"],
                    "out_of_scope_source_groups": [],
                    "op_ppu": {
                        "name": "op_ppu", "unit_identity": "op_ppu",
                        "native_operator_symbols": ["ppu_kernel"],
                        "kernel_functions": ["ppu_kernel"],
                        "kernel_launch_sites": ["kernel.cu:ppu_kernel"],
                        "public_entry_mapping": {"op_ppu": "op_ppu"},
                        "source_evidence": {"native_source_paths": ["ppu_custom/src/kernel.cu"]},
                        "variant_or_signature": "op_ppu/v1",
                        "inventory_granularity": "FINE_GRAINED",
                    },
                },
                "performance_report": {
                    "complete": True, "unit_count": 1,
                    "path": "migration_reports/performance.json",
                    "project_api_invoked": True, "public_api_invoked": True,
                    "custom_op_route_executed": True, "verified": True,
                    "baseline_device": "cuda", "custom_device": "ppu",
                    "entries": {"op_ppu": {
                        "unit_identity": "op_ppu",
                        "baseline_seconds": 0.5, "custom_seconds": 0.1, "speedup_vs_baseline": 5.0,
                        "project_api_invoked": True, "public_api_invoked": True,
                        "custom_op_route_executed": True,
                        "baseline_device": "cuda", "custom_device": "ppu",
                    }},
                    "overall_baseline_seconds": 0.5, "overall_custom_seconds": 0.1,
                    "overall_speedup_vs_baseline": 5.0,
                    "overall_project_api_invoked": True, "overall_custom_op_route_executed": True,
                    "overall_all_units_replaced": True,
                },
                "rows": [{
                    "name": "op_ppu", "status": "FULL_PASS",
                    "unit_identity": "op_ppu", "variant_or_signature": "op_ppu/v1",
                    "inventory_granularity": "FINE_GRAINED",
                    "native_operator_symbols": ["ppu_kernel"],
                    "kernel_functions": ["ppu_kernel"],
                    "kernel_launch_sites": ["kernel.cu:ppu_kernel"],
                    "public_entry_mapping": {"op_ppu": "op_ppu"},
                    "source_evidence": {"native_source_paths": ["ppu_custom/src/kernel.cu"]},
                    "opp_custom_op_artifact_evidence": {
                        "project_local": True, "in_project": True,
                        "built": True, "present": True, "loaded": True, "verified": True,
                        "project_relative_path": "ppu_custom/build/libppu.so",
                        "path": "ppu_custom/build/libppu.so",
                        "runtime_loaded_module_file": "ppu_custom/build/libppu.so",
                        "ppu_custom_op_artifact": True, "ppu_custom_op_built": True,
                        "ppu_plugin_built": True, "ppu_kernel_built": True,
                        "ppu_custom_op_loaded": True,
                        "build_provenance": {
                            "command": "nvcc -shared -o libppu.so kernel.cu",
                            "log_path": "ppu_custom/build/build.log",
                        },
                    },
                    "adapter_evidence": {"imported": True, "passed": True},
                    "parity_evidence": {"verified": True, "passed": True},
                    "integration_e2e_evidence": {
                        "passed": True, "project_api_invoked": True,
                        "public_api_invoked": True, "custom_op_route_executed": True,
                        "native_custom_op_route_executed": True,
                        "compiled_kernel_executed": True,
                    },
                    "same_run_runtime_coverage": {
                        "same_run": True, "project_api_route": True,
                        "public_api_route": True, "custom_op_route_executed": True,
                        "native_custom_op_route_executed": True,
                        "compiled_kernel_executed": True,
                        "custom_call_count": 100,
                    },
                    "performance_evidence": {
                        "baseline_seconds": 0.5, "custom_seconds": 0.1,
                        "speedup_vs_baseline": 5.0,
                        "project_api_invoked": True, "public_api_invoked": True,
                        "custom_op_route_executed": True,
                        "baseline_device": "cuda", "custom_device": "ppu",
                    },
                    "no_fallback_no_zero_call_no_builtin_contamination": {
                        "passed": True,
                        "fallback_detected": False, "zero_call_detected": False,
                        "builtin_contamination_detected": False,
                        "baseline_only_detected": False, "stub_detected": False,
                    },
                }],
            }
            (reports / "custom_op_final_gate.json").write_text(json.dumps(gate))

            contract = {
                "entry_script_kind": "custom_op_full_validation",
                "reports_dir": str(reports),
            }
            result = engine._validate_custom_op_final_gate_for_contract(contract, str(root))
            assert result is not None
            assert result["passed"] is True, f"Expected PASS with PPU policy, got errors: {result.get('errors')}"

    def test_validate_gate_legacy_none_policy_uses_npu(self):
        """_validate_custom_op_final_gate_for_contract without policy uses NPU, rejects PPU-only evidence."""
        import tempfile, json, os
        from pathlib import Path
        engine = self._mock_engine(policy=None)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "migration_reports"
            reports.mkdir()

            manifest = {"required_units": ["op_ppu"]}
            (reports / "migration_manifest.json").write_text(json.dumps(manifest))

            gate = {
                "inventory_count": 1, "manifest_entries": 1, "closed_pass_entries": 1,
                "remaining_entries": 0, "full_migration_status": "FULL_PASS",
                "project_e2e_passed": True, "report_parity_passed": True,
                "rows": [],
            }
            (reports / "custom_op_final_gate.json").write_text(json.dumps(gate))

            contract = {
                "entry_script_kind": "custom_op_full_validation",
                "reports_dir": str(reports),
            }
            result = engine._validate_custom_op_final_gate_for_contract(contract, str(root))
            assert result is not None
            assert result["passed"] is False
            # NPU legacy should reject empty rows (same behavior as before)
            assert any("non-empty" in e for e in result.get("errors", []))


class TestPerformanceValidationModes:
    """Performance validation mode: full (default), presence_only, disabled."""

    def _ppu_policy_with_mode(self, mode: str) -> object:
        from core.platform_policy import (
            TargetPlatformConfig, resolve_policy, BUILTIN_PRESETS,
        )
        tp = TargetPlatformConfig(
            "ppu_cuda_compatible",
            overrides={"custom_op_evidence": {"performance_validation": mode}},
        )
        return resolve_policy(tp, "test")

    def _minimal_valid_gate_with_perf(self, perf_evidence, overall_overrides=None):
        """Build a minimal valid PPU gate with configurable performance evidence."""
        import json, tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        reports = root / "migration_reports"
        reports.mkdir()

        manifest = {"required_units": ["op_ppu"]}
        (reports / "migration_manifest.json").write_text(json.dumps(manifest))

        build_dir = root / "ppu_custom" / "build"
        build_dir.mkdir(parents=True)
        (build_dir / "build.log").write_text("nvcc -shared -o libppu.so kernel.cu -lcuda\nppu_compiler: ok\n")
        so_path = build_dir / "libppu.so"
        so_path.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 128 + b"ppukernel\x00cuda\x00")
        src_dir = root / "ppu_custom" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "kernel.cu").write_text('#include <cuda_runtime.h>\n__global__ void ppu_kernel() {}\n')

        overall = {
            "overall_baseline_seconds": 0.5,
            "overall_custom_seconds": 0.1,
            "overall_speedup_vs_baseline": 5.0,
            "overall_project_api_invoked": True,
            "overall_custom_op_route_executed": True,
            "overall_all_units_replaced": True,
        }
        if overall_overrides:
            overall.update(overall_overrides)

        gate = {
            "inventory_count": 1, "manifest_entries": 1, "closed_pass_entries": 1,
            "remaining_entries": 0, "full_migration_status": "FULL_PASS",
            "project_e2e_passed": True, "report_parity_passed": True,
            "source_inventory": {
                "discovery_complete": True,
                "discovery_sources_checked": ["source","bindings","wrappers","autograd","aliases","launch","setup","tests"],
                "out_of_scope_source_groups": [],
                "op_ppu": {
                    "name": "op_ppu", "unit_identity": "op_ppu",
                    "native_operator_symbols": ["ppu_kernel"],
                    "kernel_functions": ["ppu_kernel"],
                    "kernel_launch_sites": ["kernel.cu:ppu_kernel"],
                    "public_entry_mapping": {"op_ppu": "op_ppu"},
                    "source_evidence": {"native_source_paths": ["ppu_custom/src/kernel.cu"]},
                    "variant_or_signature": "op_ppu/v1",
                    "inventory_granularity": "FINE_GRAINED",
                },
            },
            "performance_report": {
                "complete": True, "unit_count": 1,
                "path": "migration_reports/performance.json",
                "project_api_invoked": True, "public_api_invoked": True,
                "custom_op_route_executed": True, "verified": True,
                "baseline_device": "cuda", "custom_device": "ppu",
                "entries": {"op_ppu": {
                    "unit_identity": "op_ppu",
                    "project_api_invoked": True, "public_api_invoked": True,
                    "custom_op_route_executed": True,
                    "baseline_device": "cuda", "custom_device": "ppu",
                    **perf_evidence,
                }},
                **overall,
            },
            "rows": [{
                "name": "op_ppu", "status": "FULL_PASS",
                "unit_identity": "op_ppu", "variant_or_signature": "op_ppu/v1",
                "inventory_granularity": "FINE_GRAINED",
                "native_operator_symbols": ["ppu_kernel"],
                "kernel_functions": ["ppu_kernel"],
                "kernel_launch_sites": ["kernel.cu:ppu_kernel"],
                "public_entry_mapping": {"op_ppu": "op_ppu"},
                "source_evidence": {"native_source_paths": ["ppu_custom/src/kernel.cu"]},
                "opp_custom_op_artifact_evidence": {
                    "project_local": True, "in_project": True,
                    "built": True, "present": True, "loaded": True, "verified": True,
                    "project_relative_path": "ppu_custom/build/libppu.so",
                    "path": "ppu_custom/build/libppu.so",
                    "runtime_loaded_module_file": "ppu_custom/build/libppu.so",
                    "ppu_custom_op_artifact": True, "ppu_custom_op_built": True,
                    "ppu_plugin_built": True, "ppu_kernel_built": True,
                    "ppu_custom_op_loaded": True,
                    "build_provenance": {
                        "command": "nvcc -shared -o libppu.so kernel.cu",
                        "log_path": "ppu_custom/build/build.log",
                    },
                },
                "adapter_evidence": {"imported": True, "passed": True},
                "parity_evidence": {"verified": True, "passed": True},
                "integration_e2e_evidence": {
                    "passed": True, "project_api_invoked": True,
                    "public_api_invoked": True, "custom_op_route_executed": True,
                    "native_custom_op_route_executed": True,
                    "compiled_kernel_executed": True,
                },
                "same_run_runtime_coverage": {
                    "same_run": True, "project_api_route": True,
                    "public_api_route": True, "custom_op_route_executed": True,
                    "native_custom_op_route_executed": True,
                    "compiled_kernel_executed": True,
                    "custom_call_count": 100,
                },
                "performance_evidence": perf_evidence,
                "no_fallback_no_zero_call_no_builtin_contamination": {
                    "passed": True,
                    "fallback_detected": False, "zero_call_detected": False,
                    "builtin_contamination_detected": False,
                    "baseline_only_detected": False, "stub_detected": False,
                },
            }],
        }
        (reports / "custom_op_final_gate.json").write_text(json.dumps(gate))
        import os
        return root, gate, (lambda: os.unlink(str(root)))

    def test_full_mode_rejects_missing_speedup(self):
        """Default full mode: speedup_vs_baseline must be present and positive."""
        policy = self._ppu_policy_with_mode("full")
        perf_evidence = {
            "baseline_seconds": 0.5,
            "custom_seconds": 0.1,
            "project_api_invoked": True,
            "public_api_invoked": True,
            "custom_op_route_executed": True,
            "baseline_device": "cuda",
            "custom_device": "ppu",
        }
        root, gate, _ = self._minimal_valid_gate_with_perf(perf_evidence)
        from validators.validate_validation_final import validate_custom_op_final_gate
        result = validate_custom_op_final_gate(gate, project_root=root, platform_policy=policy)
        assert result["passed"] is False
        assert any("speedup" in e for e in result.get("errors", [])), f"Expected speedup error, got: {result.get('errors')}"

    def test_presence_only_accepts_without_speedup_when_timing_present(self):
        """presence_only: accepts baseline/custom timings without speedup fields."""
        policy = self._ppu_policy_with_mode("presence_only")
        perf_evidence = {
            "baseline_seconds": 0.5,
            "custom_seconds": 0.1,
            "project_api_invoked": True,
            "public_api_invoked": True,
            "custom_op_route_executed": True,
            "baseline_device": "cuda",
            "custom_device": "ppu",
        }
        root, gate, _ = self._minimal_valid_gate_with_perf(
            perf_evidence,
            overall_overrides={"overall_speedup_vs_baseline": None},
        )
        from validators.validate_validation_final import validate_custom_op_final_gate
        result = validate_custom_op_final_gate(gate, project_root=root, platform_policy=policy)
        assert result["passed"] is True, f"Expected PASS in presence_only mode, got: {result.get('errors')}"

    def test_presence_only_still_requires_positive_timing(self):
        """presence_only: still requires baseline_seconds > 0 and custom_seconds > 0."""
        policy = self._ppu_policy_with_mode("presence_only")
        perf_evidence = {
            "baseline_seconds": 0,
            "custom_seconds": 0,
            "project_api_invoked": True,
            "public_api_invoked": True,
            "custom_op_route_executed": True,
            "baseline_device": "cuda",
            "custom_device": "ppu",
        }
        root, gate, _ = self._minimal_valid_gate_with_perf(
            perf_evidence,
            overall_overrides={"overall_speedup_vs_baseline": None},
        )
        from validators.validate_validation_final import validate_custom_op_final_gate
        result = validate_custom_op_final_gate(gate, project_root=root, platform_policy=policy)
        assert result["passed"] is False
        assert any("missing" in e or "positive" in e for e in result.get("errors", [])), f"Expected positive timing error, got: {result.get('errors')}"

    def test_disabled_skips_performance_but_rejects_missing_no_fallback(self):
        """disabled: skips performance fields but still requires no-fallback evidence."""
        policy = self._ppu_policy_with_mode("disabled")
        perf_evidence = {
            "baseline_seconds": -1,
            "custom_seconds": -1,
        }
        root, gate, cleanup = self._minimal_valid_gate_with_perf(
            perf_evidence,
            overall_overrides={"overall_speedup_vs_baseline": None},
        )
        gate["rows"][0]["no_fallback_no_zero_call_no_builtin_contamination"] = None
        from validators.validate_validation_final import validate_custom_op_final_gate
        result = validate_custom_op_final_gate(gate, project_root=root, platform_policy=policy)
        assert result["passed"] is False
        assert any("must contain evidence" in e or "no_fallback_no_zero_call" in str(e)
                   for e in result.get("errors", [])), f"Expected no-fallback error, got: {result.get('errors')}"

    def test_disabled_accepts_missing_performance_evidence_field(self):
        """disabled: performance_evidence can be omitted from rows entirely."""
        policy = self._ppu_policy_with_mode("disabled")
        perf_evidence = {"baseline_seconds": 0.5, "custom_seconds": 0.1}
        root, gate, cleanup = self._minimal_valid_gate_with_perf(perf_evidence)
        del gate["rows"][0]["performance_evidence"]
        from validators.validate_validation_final import validate_custom_op_final_gate
        result = validate_custom_op_final_gate(gate, project_root=root, platform_policy=policy)
        assert result["passed"] is True, f"Expected PASS in disabled mode without perf evidence, got: {result.get('errors')}"


class TestCPUBaselineConfig:
    """CPU baseline device values accepted only when configured in platform policy."""

    def test_cpu_baseline_accepted_when_configured(self):
        """When policy configures cpu in performance_baseline_device_values,
        baseline_device='cpu' should be accepted."""
        from core.platform_policy import TargetPlatformConfig, resolve_policy
        from validators.validate_validation_final import _has_baseline_proof
        tp = TargetPlatformConfig(
            "ppu_cuda_compatible",
            overrides={
                "custom_op_evidence": {
                    "performance_baseline_device_values": ["cpu", "torch_cpu"],
                    "performance_baseline_boolean_fields": ["cpu_baseline"],
                }
            },
        )
        policy = resolve_policy(tp, "test")
        assert _has_baseline_proof({"baseline_device": "cpu"}, policy) is True
        assert _has_baseline_proof({"cpu_baseline": True}, policy) is True

    def test_cpu_baseline_accepted_by_default_npu_policy(self):
        """Default/legacy NPU validation uses CPU as the performance baseline."""
        from validators.validate_validation_final import _has_baseline_proof
        assert _has_baseline_proof({"baseline_device": "cpu"}, None) is True

    def test_cpu_baseline_does_not_imply_cpu_fallback_for_custom(self):
        """CPU baseline device value is accepted for BASELINE proof but custom device
        proof still requires the target accelerator device. CPU fallback is not allowed."""
        from core.platform_policy import TargetPlatformConfig, resolve_policy, get_target_device_values
        from validators.validate_validation_final import _has_target_device_custom_proof
        tp = TargetPlatformConfig(
            "ppu_cuda_compatible",
            overrides={
                "custom_op_evidence": {
                    "performance_baseline_device_values": ["cpu", "cuda"],
                }
            },
        )
        policy = resolve_policy(tp, "test")
        assert _has_target_device_custom_proof({"custom_device": "cpu"}, policy) is False
        assert _has_target_device_custom_proof({"custom_device": "ppu"}, policy) is True


def test_npu_final_gate_accepts_semicolon_separated_opp_source_paths(tmp_path):
    """Strict Ascend OPP evidence may report multiple source files as a separated string."""
    from validators.validate_validation_final import validate_custom_op_final_gate

    root = tmp_path
    reports = root / "migration_reports"
    reports.mkdir()
    (root / "opp" / "op_host").mkdir(parents=True)
    (root / "opp" / "op_kernel").mkdir(parents=True)
    (root / "opp" / "cmake_build" / "op_info").mkdir(parents=True)
    (root / "opp" / "cmake_build" / "kernel_meta").mkdir(parents=True)
    (root / "opp" / "cmake_build" / "include").mkdir(parents=True)
    (root / "opp" / "op_host" / "a_host.cpp").write_text("aclrtLaunchKernel();", encoding="utf-8")
    (root / "opp" / "op_host" / "b_host.cpp").write_text("aclrtLaunchKernel();", encoding="utf-8")
    (root / "opp" / "op_kernel" / "a.cpp").write_text('#include "kernel_operator.h"\n__aicore__ void k(){}', encoding="utf-8")
    (root / "opp" / "op_kernel" / "b.cpp").write_text('#include "kernel_operator.h"\n__aicore__ void k2(){}', encoding="utf-8")
    (root / "opp" / "build.sh").write_text("cann ascendc op_host op_kernel install", encoding="utf-8")
    (root / "opp" / "cmake_build" / "libcustom.so").write_bytes(b"\x7fELF kernel_operator aclrt op_host op_kernel aicore")
    (root / "opp" / "cmake_build" / "op_info" / "aclnn_unit.json").write_text("{}", encoding="utf-8")
    (root / "opp" / "cmake_build" / "kernel_meta" / "unit.o").write_bytes(b"kernel_operator aicore")
    (root / "opp" / "cmake_build" / "include" / "aclrtlaunch_unit.h").write_text("aclrtLaunchKernel", encoding="utf-8")
    build_log = reports / "ascendc_build.json"
    build_log.write_text("CANN OPP AscendC op_host op_kernel install package kernel_operator.h -lascendcl", encoding="utf-8")
    (reports / "performance.json").write_text(json.dumps({"unit_count": 1, "per_unit_entries": [{"unit_identity": "unit"}]}), encoding="utf-8")

    row = {
        "name": "unit",
        "unit_identity": "unit",
        "variant_or_signature": "unit/v1",
        "inventory_granularity": "fine_grained",
        "native_operator_symbols": ["unit_kernel"],
        "kernel_functions": ["unit_kernel"],
        "kernel_launch_sites": ["opp/op_kernel/a.cpp:unit_kernel"],
        "public_entry_mapping": {"python_api": "ops.unit"},
        "source_evidence": ["opp/op_kernel/a.cpp"],
        "status": "PASS",
        "opp_custom_op_artifact_evidence": {
            "project_local": True,
            "built": True,
            "loaded": True,
            "project_relative_path": "opp/cmake_build/libcustom.so",
            "runtime_loaded_module_file": "opp/cmake_build/libcustom.so",
            "op_host": "opp/op_host/a_host.cpp; opp/op_host/b_host.cpp",
            "op_kernel": "opp/op_kernel/a.cpp; opp/op_kernel/b.cpp",
            "opp_build_script": "opp/build.sh",
            "generated_header_paths": ["opp/cmake_build/include/aclrtlaunch_unit.h"],
            "op_info_paths": ["opp/cmake_build/op_info/aclnn_unit.json"],
            "kernel_meta_paths": ["opp/cmake_build/kernel_meta/unit.o"],
            "opp_install_evidence": {"path": "opp/cmake_build"},
            "build_provenance": {"command": "bash opp/build.sh", "log_path": "migration_reports/ascendc_build.json"},
        },
        "adapter_evidence": {"imported": True, "passed": True},
        "parity_evidence": {"verified": True, "passed": True},
        "integration_e2e_evidence": {
            "project_api_invoked": True,
            "custom_op_route_executed": True,
            "native_custom_op_route_executed": True,
            "compiled_kernel_executed": True,
        },
        "public_api_route_evidence": {
            "project_api_invoked": True,
            "custom_op_route_executed": True,
            "native_custom_op_route_executed": True,
            "same_run": True,
            "custom_call_count": 1,
        },
        "framework_integration_route_evidence": {
            "project_api_invoked": True,
            "custom_op_route_executed": True,
            "native_custom_op_route_executed": True,
            "compiled_kernel_executed": True,
        },
        "same_run_runtime_coverage": {
            "same_run": True,
            "custom_call_count": 1,
            "project_api_route": True,
            "custom_op_route_executed": True,
            "native_custom_op_route_executed": True,
            "compiled_kernel_executed": True,
        },
        "performance_evidence": {
            "baseline_seconds": 1.0,
            "custom_seconds": 0.5,
            "speedup_vs_baseline": 2.0,
            "baseline_device": "cpu",
            "custom_device": "npu",
            "cpu_baseline_invoked": True,
            "npu_custom_invoked": True,
            "project_api_invoked": True,
            "custom_op_route_executed": True,
        },
        "no_fallback_no_zero_call_no_builtin_contamination": {
            "passed": True,
            "fallback_detected": False,
            "zero_call_detected": False,
            "builtin_contamination_detected": False,
            "baseline_only_detected": False,
            "stub_detected": False,
        },
    }
    manifest = {"required_units": ["unit"]}
    (reports / "migration_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    gate = {
        "inventory_count": 1,
        "manifest_entries": 1,
        "closed_pass_entries": 1,
        "remaining_entries": 0,
        "full_migration_status": "FULL_PASS",
        "project_e2e_passed": True,
        "report_parity_passed": True,
        "source_inventory": {
            "discovery_complete": True,
            "discovery_sources_checked": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
            "out_of_scope_source_groups": [],
            "entries": [
                {
                    "unit_identity": "unit",
                    "name": "unit",
                    "variant_or_signature": "unit/v1",
                    "inventory_granularity": "fine_grained",
                    "native_operator_symbols": ["unit_kernel"],
                    "kernel_functions": ["unit_kernel"],
                    "kernel_launch_sites": ["opp/op_kernel/a.cpp:unit_kernel"],
                    "public_entry_mapping": {"python_api": "ops.unit"},
                    "source_evidence": ["opp/op_kernel/a.cpp"],
                }
            ],
        },
        "performance_report": {
            "complete": True,
            "unit_count": 1,
            "path": "migration_reports/performance.json",
            "project_relative_path": "migration_reports/performance.json",
            "report_path": "migration_reports/performance.json",
            "project_api_invoked": True,
            "public_api_invoked": True,
            "custom_op_route_executed": True,
            "verified": True,
            "baseline_device": "cpu",
            "custom_device": "npu",
            "cpu_baseline_invoked": True,
            "npu_custom_invoked": True,
            "overall_baseline_seconds": 1.0,
            "overall_custom_seconds": 0.5,
            "overall_speedup_vs_baseline": 2.0,
            "overall_project_api_invoked": True,
            "overall_custom_op_route_executed": True,
            "overall_all_units_replaced": True,
            "entries": {
                "unit": {
                    "unit_identity": "unit",
                    "baseline_seconds": 1.0,
                    "custom_seconds": 0.5,
                    "speedup_vs_baseline": 2.0,
                    "baseline_device": "cpu",
                    "custom_device": "npu",
                    "cpu_baseline_invoked": True,
                    "npu_custom_invoked": True,
                    "project_api_invoked": True,
                    "custom_op_route_executed": True,
                }
            },
        },
        "rows": [row],
    }

    result = validate_custom_op_final_gate(gate, project_root=root)
    assert result["passed"] is True, result["errors"]


class TestUnitIdentityMatching:
    """unit_identity is accepted for row/source/manifest identity matching."""

    def test_unit_identity_accepted_as_row_name(self):
        """Rows with unit_identity but no 'name' field should be matched correctly."""
        from validators.validate_validation_final import _extract_row_name
        assert _extract_row_name({"unit_identity": "op_foo"}) == "op_foo"
        assert _extract_row_name({"unit_identity": "op_foo", "name": "other_name"}) == "op_foo"

    def test_unit_identity_accepted_in_inventory_extraction(self):
        """Inventory entries with unit_identity should be keyed by it."""
        from validators.validate_validation_final import _extract_inventory_entries
        inventory = {
            "discovery_complete": True,
            "discovery_sources_checked": ["source"],
            "out_of_scope_source_groups": [],
            "op_bar": {"unit_identity": "op_bar"},
        }
        entries = _extract_inventory_entries(inventory)
        assert "op_bar" in entries

    def test_unit_identity_matching_passes_with_name_equals_unit_identity(self):
        """Full gate with unit_identity == name passes source_inventory matching."""
        import json, tempfile
        from pathlib import Path
        from core.platform_policy import BUILTIN_PRESETS
        from validators.validate_validation_final import validate_custom_op_final_gate

        ppu = BUILTIN_PRESETS["ppu_cuda_compatible"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "migration_reports"
            reports.mkdir()

            manifest = {"required_units": ["op_foo"]}
            (reports / "migration_manifest.json").write_text(json.dumps(manifest))

            build_dir = root / "ppu_custom" / "build"
            build_dir.mkdir(parents=True)
            (build_dir / "build.log").write_text("nvcc -shared -o libppu.so kernel.cu -lcuda\nppu_compiler: ok\n")
            so_path = build_dir / "libppu.so"
            so_path.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 128 + b"ppukernel\x00cuda\x00")
            src_dir = root / "ppu_custom" / "src"
            src_dir.mkdir(parents=True)
            (src_dir / "kernel.cu").write_text('#include <cuda_runtime.h>\n__global__ void ppu_kernel() {}\n')

            gate = {
                "inventory_count": 1, "manifest_entries": 1, "closed_pass_entries": 1,
                "remaining_entries": 0, "full_migration_status": "FULL_PASS",
                "project_e2e_passed": True, "report_parity_passed": True,
                "source_inventory": {
                    "discovery_complete": True,
                    "discovery_sources_checked": ["source","bindings","wrappers","autograd","aliases","launch","setup","tests"],
                    "out_of_scope_source_groups": [],
                    "op_foo": {
                        "unit_identity": "op_foo",
                        "native_operator_symbols": ["op_foo_impl"],
                        "kernel_functions": ["ppu_kernel"],
                        "kernel_launch_sites": ["kernel.cu:ppu_kernel"],
                        "public_entry_mapping": {"op_foo": "op_foo"},
                        "source_evidence": {"native_source_paths": ["ppu_custom/src/kernel.cu"]},
                        "variant_or_signature": "op_foo/v1",
                        "inventory_granularity": "FINE_GRAINED",
                    },
                },
                "performance_report": {
                    "complete": True, "unit_count": 1,
                    "path": "migration_reports/performance.json",
                    "project_api_invoked": True, "public_api_invoked": True,
                    "custom_op_route_executed": True, "verified": True,
                    "baseline_device": "cuda", "custom_device": "ppu",
                    "entries": {"op_foo": {
                        "unit_identity": "op_foo",
                        "baseline_seconds": 0.5, "custom_seconds": 0.1,
                        "speedup_vs_baseline": 5.0,
                        "project_api_invoked": True, "public_api_invoked": True,
                        "custom_op_route_executed": True,
                        "baseline_device": "cuda", "custom_device": "ppu",
                    }},
                    "overall_baseline_seconds": 0.5, "overall_custom_seconds": 0.1,
                    "overall_speedup_vs_baseline": 5.0,
                    "overall_project_api_invoked": True, "overall_custom_op_route_executed": True,
                    "overall_all_units_replaced": True,
                },
                "rows": [{
                    "unit_identity": "op_foo",
                    "status": "FULL_PASS",
                    "variant_or_signature": "op_foo/v1",
                    "inventory_granularity": "FINE_GRAINED",
                    "native_operator_symbols": ["op_foo_impl"],
                    "kernel_functions": ["ppu_kernel"],
                    "kernel_launch_sites": ["kernel.cu:ppu_kernel"],
                    "public_entry_mapping": {"op_foo": "op_foo"},
                    "source_evidence": {"native_source_paths": ["ppu_custom/src/kernel.cu"]},
                    "opp_custom_op_artifact_evidence": {
                        "project_local": True, "in_project": True,
                        "built": True, "present": True, "loaded": True, "verified": True,
                        "project_relative_path": "ppu_custom/build/libppu.so",
                        "path": "ppu_custom/build/libppu.so",
                        "runtime_loaded_module_file": "ppu_custom/build/libppu.so",
                        "ppu_custom_op_artifact": True, "ppu_custom_op_built": True,
                        "ppu_plugin_built": True, "ppu_kernel_built": True,
                        "ppu_custom_op_loaded": True,
                        "build_provenance": {
                            "command": "nvcc -shared -o libppu.so kernel.cu",
                            "log_path": "ppu_custom/build/build.log",
                        },
                    },
                    "adapter_evidence": {"imported": True, "passed": True},
                    "parity_evidence": {"verified": True, "passed": True},
                    "integration_e2e_evidence": {
                        "passed": True, "project_api_invoked": True,
                        "public_api_invoked": True, "custom_op_route_executed": True,
                        "native_custom_op_route_executed": True,
                        "compiled_kernel_executed": True,
                    },
                    "same_run_runtime_coverage": {
                        "same_run": True, "project_api_route": True,
                        "public_api_route": True, "custom_op_route_executed": True,
                        "native_custom_op_route_executed": True,
                        "compiled_kernel_executed": True,
                        "custom_call_count": 100,
                    },
                    "performance_evidence": {
                        "baseline_seconds": 0.5, "custom_seconds": 0.1,
                        "speedup_vs_baseline": 5.0,
                        "project_api_invoked": True, "public_api_invoked": True,
                        "custom_op_route_executed": True,
                        "baseline_device": "cuda", "custom_device": "ppu",
                    },
                    "no_fallback_no_zero_call_no_builtin_contamination": {
                        "passed": True,
                        "fallback_detected": False, "zero_call_detected": False,
                        "builtin_contamination_detected": False,
                        "baseline_only_detected": False, "stub_detected": False,
                    },
                }],
            }
            (reports / "custom_op_final_gate.json").write_text(json.dumps(gate))

            result = validate_custom_op_final_gate(gate, project_root=root, platform_policy=ppu)
            assert result["passed"] is True, f"Expected PASS with unit_identity-only rows, got: {result.get('errors')}"

    def test_name_unit_identity_mismatch_in_inventory_is_rejected(self):
        """Source_inventory entry with name != unit_identity must be rejected."""
        from validators.validate_validation_final import _validate_fine_grained_inventory_entry
        entry = {
            "name": "other_name",
            "unit_identity": "op_foo",
            "native_operator_symbols": ["op_foo_impl"],
            "kernel_functions": ["kern"],
            "source_evidence": {"src": "foo"},
            "variant_or_signature": "v1",
            "kernel_launch_sites": ["site"],
            "public_entry_mapping": {"a": "b"},
            "inventory_granularity": "FINE_GRAINED",
        }
        errors: list[str] = []
        _validate_fine_grained_inventory_entry(entry, "entries[op_foo]", errors)
        assert any("unit_identity must match" in e for e in errors), (
            f"Expected mismatch error, got: {errors}"
        )
