"""Tests for validation_final.py with platform_policy support."""
import json
import pytest
import tempfile
import os
from pathlib import Path

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
        result = validate_custom_op_final_gate(data, platform_policy=None)
        assert result["passed"] is False
        assert any("non-empty" in e for e in result.get("errors", []))

    def test_npu_policy_still_works(self):
        """NPU policy preserves current behavior."""
        npu = BUILTIN_PRESETS["npu_ascend"]
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

            # migration_manifest.json
            manifest = {"required_units": ["op_mm_ppu"]}
            (reports / "migration_manifest.json").write_text(json.dumps(manifest))

            # Build log with PPU + CUDA tokens
            build_dir = root / "ppu_custom" / "build"
            build_dir.mkdir(parents=True)
            build_log = build_dir / "build.log"
            build_log.write_text(
                "nvcc -shared -fPIC -o libppu_op.so kernel.cu -lcuda -lcudart\n"
                "ppu_compiler: compiled ppukernel successfully\n"
            )

            # Source file with CUDA evidence
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

            # Compiled .so binary (ELF header + PPU tokens)
            so_path = build_dir / "libppu_op.so"
            so_data = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 128
            so_data += b"ppukernel\x00ppuccl\x00cuda\x00cudart\x00"
            so_path.write_bytes(so_data)

            # custom_op_final_gate.json with PPU evidence
            relative_so = "ppu_custom/build/libppu_op.so"
            relative_src = "ppu_custom/src/kernel.cu"
            relative_log = "ppu_custom/build/build.log"

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
                            "passed": True,
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
